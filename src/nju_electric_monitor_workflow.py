#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
南京大学电费充值页面剩余电量监控脚本（自动无头模式）
支持配置文件和自动验证码识别
"""

# 导入PIL兼容性补丁
try:
    from pil_compatibility_patch import *
except ImportError:
    pass

import warnings
warnings.filterwarnings("ignore")

import time
import re
import json
import os
from datetime import datetime
try:
    # Python 3.9+ 内置 zoneinfo
    from zoneinfo import ZoneInfo
    BEIJING_TZ = ZoneInfo("Asia/Shanghai")
except Exception:
    # 如果 zoneinfo 不可用，回退到 UTC 并记录（最终仍会生成时间，但无时区偏移）
    BEIJING_TZ = None
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import logging
from PIL import Image
import io
import ddddocr
import getpass

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import MaxNLocator
import matplotlib.font_manager as fm
import numpy as np
import plotly.graph_objs as go

# PIL兼容性补丁 - 解决ANTIALIAS被弃用的问题
try:
    from PIL import Image
    if not hasattr(Image, 'ANTIALIAS'):
        Image.ANTIALIAS = Image.Resampling.LANCZOS
except ImportError:
    pass

class NJUElectricMonitor:
    def __init__(self, config_file="config_workflow.json"):
        """初始化监控器"""
        self.url = "https://epay.nju.edu.cn/epay/h5/nju/electric/index"
        self.config_file = config_file
        self.config = self.load_config()
        # 优先从环境变量读取凭据（由 GitHub Actions 注入），若不存在则使用配置文件中的值
        self.username = os.environ.get('NJU_USERNAME', self.config.get("username", ""))
        self.password = os.environ.get('NJU_PASSWORD', self.config.get("password", ""))
        self.auto_login = self.config.get("auto_login", True)
        self.headless_mode = self.config.get("headless_mode", True)
        # 默认验证码重试次数从 5 次降为 3 次，可在配置文件中通过 captcha_retry_count 覆盖
        self.captcha_retry_count = self.config.get("captcha_retry_count", 3)
        self.captcha_confidence_threshold = self.config.get("captcha_confidence_threshold", 0.3)
        self.save_captcha_images = self.config.get("save_captcha_images", True)
        self.driver = None
        self.wait = None
        self.ocr_reader = None
        # workflow 专用验证码图片目录（在 run() 中初始化与清空）
        self.qr_pics_dir = None
        
        # 设置日志级别
        log_level = getattr(logging, self.config.get("log_level", "INFO"))
        self.setup_logging(log_level)
        
        self.setup_driver()
        self.setup_ocr()

    def init_qr_pics_dir(self):
        """初始化并清空 workflow 运行使用的验证码图片目录 data/qr_pics_workflow"""
        try:
            base_dir = os.path.join(os.path.dirname(__file__), "..", "data")
            qr_dir = os.path.join(base_dir, "qr_pics_workflow")
            os.makedirs(qr_dir, exist_ok=True)

            # 清空目录下旧图片
            for name in os.listdir(qr_dir):
                path = os.path.join(qr_dir, name)
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                except Exception:
                    # 清理失败不影响主流程
                    continue

            self.qr_pics_dir = qr_dir
            self.logger.info(f"已初始化并清空验证码图片目录: {qr_dir}")
        except Exception as e:
            # 目录初始化失败不应阻塞主流程，仅记录告警
            if hasattr(self, "logger"):
                self.logger.warning(f"初始化验证码图片目录失败: {e}")
        
    def setup_logging(self, log_level):
        """设置日志（按 年-月-日-时 生成文件名）"""
        log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
        os.makedirs(log_dir, exist_ok=True)
        # 使用北京时间（Asia/Shanghai）作为日志文件名时间来源
        now = datetime.now(BEIJING_TZ) if BEIJING_TZ else datetime.now()
        log_filename = f"nju_electric_monitor-{now.strftime('%Y-%m-%d-%H')}.log"
        log_path = os.path.join(log_dir, log_filename)
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_path, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        # 尝试设置 matplotlib 字体回退（确保日志可见时记录）
        try:
            self.setup_fonts()
        except Exception:
            # 字体设置不应阻塞主流程
            self.logger.debug("字体初始化时出现异常，继续运行")
        
    def load_config(self):
        """加载配置文件：先从仓库根目录的指定文件读取，不覆盖 username/password"""
        try:
            # 支持相对路径，以 src 目录为基准回退到仓库根目录
            config_path = os.path.join(os.path.dirname(__file__), '..', self.config_file)
            config_path = os.path.abspath(config_path)
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                    return cfg if isinstance(cfg, dict) else {}
            else:
                # 创建默认配置文件
                default_config = {
                    "username": "",
                    "password": "",
                    "auto_login": True,
                    "headless_mode": True,
                    "captcha_retry_count": 10,
                    "log_level": "INFO"
                }
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, indent=4, ensure_ascii=False)
                return default_config
        except Exception as e:
            print(f"加载配置文件失败: {e}")
            return {}
        
    def save_config(self):
        """保存配置文件（注意不要提交 username/password 到仓库）"""
        try:
            self.config["username"] = ""
            self.config["password"] = ""
            config_path = os.path.join(os.path.dirname(__file__), '..', self.config_file)
            config_path = os.path.abspath(config_path)
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            self.logger and self.logger.warning(f"保存配置文件失败: {e}")
        
    def setup_driver(self):
        """设置Chrome浏览器驱动"""
        import platform, shutil
        chrome_options = Options()
        if self.headless_mode:
            # 在新版本 Chrome 中使用 --headless=new 更稳定
            try:
                chrome_options.add_argument("--headless=new")
            except Exception:
                chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-plugins")
        try:
            # 根据平台选择 driver：Windows 优先使用仓库内的 chromedriver；Linux/macOS 优先使用系统 PATH 中的 chromedriver
            driver_path = None
            system = platform.system().lower()
            if system.startswith('win'):
                chromedriver_path = os.path.join(os.path.dirname(__file__), '..', 'chromedriver-win64', 'chromedriver.exe')
                if os.path.exists(chromedriver_path):
                    driver_path = chromedriver_path
            else:
                # 尝试系统 chromedriver
                possible = shutil.which('chromedriver') or shutil.which('chromedriver.exe')
                if possible:
                    driver_path = possible
                else:
                    # 尝试仓库内的可执行（如果存在并可执行）
                    repo_driver = os.path.join(os.path.dirname(__file__), '..', 'chromedriver-win64', 'chromedriver.exe')
                    if os.path.exists(repo_driver):
                        driver_path = repo_driver

            if driver_path:
                # 在非 Windows 平台上确保可执行权限
                try:
                    if not system.startswith('win'):
                        os.chmod(driver_path, 0o755)
                except Exception:
                    pass
                service = Service(driver_path)
                self.driver = webdriver.Chrome(service=service, options=chrome_options)
                self.logger.info(f"使用 ChromeDriver: {driver_path}")
            else:
                # 依赖 PATH 中的 chromedriver 或使用 webdriver-manager 等方式
                self.driver = webdriver.Chrome(options=chrome_options)
                self.logger.info("使用系统 PATH 中的 ChromeDriver 或内置驱动")

            self.wait = WebDriverWait(self.driver, 20)
        except Exception as e:
            self.logger.error(f"浏览器驱动初始化失败: {e}")
            raise
        
    def setup_ocr(self):
        """设置OCR识别器（使用 ddddocr，免模型文件管理）"""
        try:
            # 使用验证码专用的 ddddocr 引擎，无需单独下载/管理模型文件
            self.ocr_reader = ddddocr.DdddOcr(show_ad=False)
            self.logger.info("ddddocr 识别器初始化成功")
        except Exception as e:
            self.logger.error(f"ddddocr 识别器初始化失败: {e}")
            raise
    
    def setup_fonts(self):
        """为 matplotlib 配置可用的中英文字体回退列表，并记录选中的字体供调试。

        优先尝试常见 Windows 字体，然后尝试在 Linux runner 常见的 Noto/WenQuanYi/DejaVu 系列。
        """
        try:
            # 优先级列表（按首选到次选）
            preferred_fonts = [
                'Microsoft YaHei',
                'Segoe UI',
                'Arial Unicode MS',
                'Noto Sans CJK SC',
                'Noto Sans CJK JP',
                'Noto Sans',
                'WenQuanYi Micro Hei',
                'WenQuanYi Zen Hei',
                'DejaVu Sans',
                'Arial'
            ]

            # 使用已导入的 plt 设置 rcParams
            try:
                plt.rcParams['font.family'] = 'sans-serif'
                plt.rcParams['font.sans-serif'] = preferred_fonts
            except Exception:
                # 如果 plt 不可用，跳过字体设置
                self.logger.debug('plt 或 matplotlib 不可用，跳过字体设置')

            # 记录第一个可用字体（用于调试日志）
            available = None
            for fname in preferred_fonts:
                try:
                    fpath = fm.findfont(fname, fallback_to_default=False)
                    if fpath and os.path.exists(fpath):
                        available = fname
                        break
                except Exception:
                    continue

            if available:
                self.logger.info(f"Matplotlib 字体设置：使用回退字体列表，首选可用字体: {available}")
            else:
                self.logger.warning("Matplotlib 未找到首选中英文字体，已设置回退列表，但渲染可能仍使用默认字体")
        except Exception as e:
            try:
                self.logger.warning(f"设置 matplotlib 字体回退时出错: {e}")
            except Exception:
                pass
    
    def get_user_credentials(self):
        """获取用户登录凭据（在非交互环境下不提示保存）"""
        import sys
        # 如果已从环境变量或配置中得到凭据，则不会提示输入
        if not self.username:
            # 仅在交互式终端才请求输入
            if sys.stdin.isatty():
                self.username = input("请输入用户名: ").strip()
            else:
                self.logger.warning("非交互环境且未提供用户名，无法继续")
                return
        if not self.password:
            if sys.stdin.isatty():
                self.password = getpass.getpass("请输入密码: ")
            else:
                self.logger.warning("非交互环境且未提供密码，无法继续")
                return
    
    def wait_for_login_form(self):
        """等待登录表单加载"""
        try:
            self.logger.info("等待登录表单加载...")
            # 等待用户名输入框出现
            username_input = self.wait.until(
                EC.presence_of_element_located((By.ID, "username"))
            )
            self.logger.info("登录表单已加载")
            return True
        except TimeoutException:
            self.logger.warning("登录表单加载超时")
            return False
    
    def fill_login_form(self):
        """填写登录表单"""
        try:
            self.logger.info("开始填写登录表单...")
            
            # 填写用户名 - 使用精确的ID选择器
            try:
                username_input = self.driver.find_element(By.ID, "username")
                username_input.clear()
                username_input.send_keys(self.username)
                self.logger.info("用户名填写完成")
            except NoSuchElementException:
                self.logger.error("未找到用户名输入框")
                return False
            
            # 填写密码 - 使用精确的ID选择器
            try:
                password_input = self.driver.find_element(By.ID, "password")
                password_input.clear()
                password_input.send_keys(self.password)
                self.logger.info("密码填写完成")
            except NoSuchElementException:
                self.logger.error("未找到密码输入框")
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"填写登录表单时出错: {e}")
            return False
    
    def capture_captcha_image(self):
        """捕获验证码图片（直接截取页面元素）"""
        try:
            self.logger.info("查找验证码图片...")
            try:
                captcha_elem = self.driver.find_element(By.ID, "captchaImg")
                if captcha_elem.is_displayed():
                    self.logger.info("找到验证码图片，开始截图...")
                    img_bytes = captcha_elem.screenshot_as_png
                    img = Image.open(io.BytesIO(img_bytes))
                    self.logger.info("验证码图片截取成功")
                    return captcha_elem, img
                else:
                    self.logger.warning("验证码图片不可见")
                    return None, None
            except NoSuchElementException:
                self.logger.warning("未找到验证码图片")
                return None, None
        except Exception as e:
            self.logger.error(f"捕获验证码图片时出错: {e}")
            return None, None

    def recognize_captcha(self, captcha_img):
        """识别验证码（使用 ddddocr，简化为直接整图分类）。"""
        try:
            if not captcha_img:
                return None

            if not self.ocr_reader:
                self.logger.error("ddddocr 未初始化")
                return None

            self.logger.info("开始使用 ddddocr 识别验证码...")

            # 将 PIL 图像编码为 PNG 字节流供 ddddocr 识别
            buf = io.BytesIO()
            try:
                img_rgb = captcha_img.convert('RGB')
            except Exception:
                img_rgb = captcha_img
            img_rgb.save(buf, format="PNG")
            img_bytes = buf.getvalue()

            try:
                raw_text = self.ocr_reader.classification(img_bytes)
            except Exception as e:
                self.logger.error(f"ddddocr 识别出错: {e}")
                return None

            if not raw_text:
                self.logger.warning("ddddocr 未返回任何结果")
                return None

            # 清洗并规范化结果：仅保留字母数字，统一为大写
            text_clean = re.sub(r"[^A-Za-z0-9]", "", str(raw_text).strip())
            text_clean = text_clean.upper()

            if not text_clean:
                self.logger.warning(f"ddddocr 结果无有效字符: raw={raw_text!r}")
                return None

            if len(text_clean) != 4:
                self.logger.info(f"ddddocr 返回长度为 {len(text_clean)} 的结果: {text_clean!r}，尝试截取前4位")
                if len(text_clean) >= 4:
                    text_clean = text_clean[:4]
                else:
                    # 长度不足4，交由外层重试逻辑
                    return None

            self.logger.info(f"ddddocr 最终验证码识别结果: {text_clean}")
            return text_clean

        except Exception as e:
            self.logger.error(f"识别验证码时出错: {e}")
            return None
    
    def preprocess_captcha_image(self, img):
        """预处理验证码图像 - 采用轻预处理策略避免过度处理导致字符失真"""
        try:
            # 策略：先轻度增强，避免过度二值化导致字符丢失
            # 1. 转为 RGB 确保格式一致
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # 2. 调整大小以提升 OCR 精度（使用高质量重采样）
            try:
                img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
            except AttributeError:
                img = img.resize((img.width * 2, img.height * 2), Image.ANTIALIAS)
            
            # 3. 对比度增强（轻度）
            try:
                from PIL import ImageEnhance
                enhancer = ImageEnhance.Contrast(img)
                img = enhancer.enhance(1.3)  # 轻度增强
            except Exception:
                pass
            
            # 4. 尝试 OpenCV 轻量级处理（如果可用）
            try:
                import cv2
                import numpy as _np
                img_cv = _np.array(img)
                # 轻度高斯模糊去噪
                img_cv = cv2.GaussianBlur(img_cv, (3, 3), 0.5)
                return Image.fromarray(img_cv)
            except Exception:
                # OpenCV 不可用或处理失败，直接返回增强后的图像
                return img
            
        except Exception as e:
            self.logger.warning(f"图像预处理失败: {e}")
            return img
    
    def generate_alternative_images(self, img):
        """生成多种处理后的图像用于识别"""
        alternative_images = []
        
        try:
            # 原始图像
            alternative_images.append(img)
            
            # 灰度图像
            if img.mode != 'L':
                alternative_images.append(img.convert('L'))
            
            # 放大图像 - 使用兼容的重采样方法
            try:
                enlarged = img.resize((img.width * 3, img.height * 3), Image.Resampling.LANCZOS)
            except AttributeError:
                # 如果Resampling不可用，使用ANTIALIAS
                enlarged = img.resize((img.width * 3, img.height * 3), Image.ANTIALIAS)
            alternative_images.append(enlarged)
            
            # 高对比度图像
            from PIL import ImageEnhance
            enhancer = ImageEnhance.Contrast(img)
            high_contrast = enhancer.enhance(2.0)
            alternative_images.append(high_contrast)
            
            # 锐化图像
            from PIL import ImageFilter
            sharpened = img.filter(ImageFilter.SHARPEN)
            alternative_images.append(sharpened)

            # 尝试基于 OpenCV 的自适应预处理变体（若可用）
            try:
                pre_cv = self.preprocess_captcha_image(img)
                if pre_cv is not None:
                    alternative_images.append(pre_cv)
            except Exception:
                pass
            
        except Exception as e:
            self.logger.warning(f"生成替代图像失败: {e}")
        
        return alternative_images

    def get_captcha_element(self):
        """返回验证码的 WebElement（如果存在），否则返回 None"""
        try:
            return self.driver.find_element(By.ID, "captchaImg")
        except Exception:
            return None

    def wait_for_captcha_refresh(self, prev_elem, timeout=8):
        """等待验证码元素刷新：优先等待 prev_elem 变为 stale，否则等待 src 属性变化。"""
        try:
            if prev_elem is None:
                return self.get_captcha_element()
            try:
                # 等待旧元素变为 stale（被替换）
                WebDriverWait(self.driver, timeout).until(EC.staleness_of(prev_elem))
                return self.get_captcha_element()
            except Exception:
                # fallback: 等待 src 改变
                try:
                    prev_src = prev_elem.get_attribute('src')
                    def src_changed(driver):
                        el = self.get_captcha_element()
                        if not el:
                            return False
                        new_src = el.get_attribute('src')
                        return new_src and new_src != prev_src
                    WebDriverWait(self.driver, timeout).until(src_changed)
                    return self.get_captcha_element()
                except Exception:
                    return self.get_captcha_element()
        except Exception:
            return None

    def char_level_vote(self, candidates, target_len=4):
        """简单的字符级加权投票，用于在没有明确长度为 target_len 的候选时合成结果

        candidates: [{'text': str, 'prob': float, ...}, ...]
        """
        try:
            from collections import Counter
            # 过滤掉空文本
            cand_texts = [(c['text'], float(c.get('prob', 0.0))) for c in candidates if c.get('text')]
            if not cand_texts:
                return None

            # 使用出现频率最高的长度作为目标长度（优先 target_len）
            lengths = [len(t) for t, _ in cand_texts]
            if target_len not in lengths:
                # 允许偏差：选择最常见长度，或使用目标长度
                from statistics import mode
                try:
                    most_common_len = mode(lengths)
                except Exception:
                    most_common_len = max(set(lengths), key=lengths.count)
                target = target_len if target_len in lengths else most_common_len
            else:
                target = target_len

            # 对每个字符位置做加权投票
            result_chars = []
            for i in range(target):
                votes = Counter()
                for txt, prob in cand_texts:
                    if i < len(txt):
                        votes[txt[i]] += prob
                if votes:
                    result_chars.append(votes.most_common(1)[0][0])
                else:
                    # 没有投票，填空
                    result_chars.append('')

            res = ''.join(result_chars).strip()
            # 清理非字母数字
            res = re.sub(r'[^A-Za-z0-9]', '', res)
            return res if res else None
        except Exception:
            return None
    
    def fill_captcha(self, captcha_text):
        """填写验证码"""
        try:
            if not captcha_text:
                self.logger.warning("没有验证码文本可填写")
                return False
            
            self.logger.info("查找验证码输入框...")
            captcha_input = None

            # 1) 首选旧版精确 ID（向后兼容）
            try:
                captcha_input = self.driver.find_element(By.ID, "captchaResponse")
            except NoSuchElementException:
                captcha_input = None

            # 2) 若找不到，使用更宽松的 XPath 规则：id/name/placeholder 中包含 "captcha" 或 "验证码"
            if captcha_input is None:
                try:
                    xpath = "//input[" \
                            "contains(translate(@id,'CAPTCHA','captcha'),'captcha') or " \
                            "contains(translate(@name,'CAPTCHA','captcha'),'captcha') or " \
                            "contains(@placeholder,'验证码')" \
                            "]"
                    candidates = self.driver.find_elements(By.XPATH, xpath)
                    visible = [el for el in candidates if el.is_displayed() and el.is_enabled()]
                    if visible:
                        captcha_input = visible[0]
                        self.logger.info(f"通过 XPath 找到 {len(visible)} 个验证码候选输入框，使用第一个。")
                    else:
                        if candidates:
                            self.logger.warning(f"找到 {len(candidates)} 个疑似验证码输入框，但均不可见或不可用。")
                except Exception as e:
                    self.logger.warning(f"通过 XPath 查找验证码输入框时出错: {e}")

            if captcha_input is None:
                self.logger.error("未找到验证码输入框（ID 和 XPath 都未命中）")
                return False

            try:
                captcha_input.clear()
            except Exception:
                pass
            captcha_input.send_keys(captcha_text)
            self.logger.info("验证码填写完成")
            return True
                
        except Exception as e:
            self.logger.error(f"填写验证码时出错: {e}")
            return False
    
    def handle_captcha(self):
        """处理验证码（两层嵌套重试：页面刷新 + 同图多次 OCR）"""
        try:
            self.logger.info("开始处理验证码（两层嵌套重试）...")
            outer_max = max(1, int(self.captcha_retry_count))
            inner_max = 3  # 同一验证码图片下的 OCR 尝试次数
            last_captcha_img = None

            for outer in range(outer_max):
                self.logger.info(f"外层页面重试 {outer + 1}/{outer_max}")

                # 外层：重新加载或刷新页面，并填写用户名密码
                try:
                    if outer == 0:
                        # 假设 run() 已经打开了页面，但仍确保表单加载完毕
                        if not self.wait_for_login_form():
                            self.logger.warning("登录表单未就绪，尝试刷新页面")
                            self.driver.refresh()
                            time.sleep(2)
                            if not self.wait_for_login_form():
                                self.logger.warning("刷新后登录表单仍未加载，跳过本轮外层重试")
                                continue
                    else:
                        # 后续外层尝试直接刷新页面
                        try:
                            self.driver.refresh()
                        except Exception as e:
                            self.logger.warning(f"刷新页面失败: {e}")
                        time.sleep(2)
                        if not self.wait_for_login_form():
                            self.logger.warning("刷新后登录表单加载失败，跳过本轮外层重试")
                            continue

                    if not self.fill_login_form():
                        self.logger.warning("填写登录表单失败，跳过本轮外层重试")
                        continue
                except Exception as e:
                    self.logger.warning(f"准备登录表单时出错: {e}")
                    continue

                # 获取当前页面上的验证码图片
                prev_elem, captcha_img = self.capture_captcha_image()
                last_captcha_img = captcha_img
                if not captcha_img:
                    self.logger.info("未检测到验证码图片，可能无需验证码，直接尝试点击登录按钮")
                    if self.click_login_button():
                        # 后续由 wait_for_login_success 判断是否真正登录成功
                        return True
                    else:
                        self.logger.error("未检测到验证码图片但点击登录按钮失败")
                        continue

                # 保存验证码图片用于调试（只保存当前外层的一张）
                captcha_path_for_outer = None
                if self.save_captcha_images:
                    try:
                        # 1) 仍然保存一份统一的调试文件
                        debug_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'captcha_debug.png')
                        captcha_img.save(debug_path)
                        self.logger.info(f"验证码图片已保存到 {debug_path}")

                        # 2) 额外保存到 workflow 专用目录，并在识别成功后按验证码结果重命名
                        if not self.qr_pics_dir:
                            base_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
                            self.qr_pics_dir = os.path.join(base_dir, 'qr_pics_workflow')
                            os.makedirs(self.qr_pics_dir, exist_ok=True)

                        filename = f"captcha_outer{outer + 1}.png"
                        captcha_path_for_outer = os.path.join(self.qr_pics_dir, filename)
                        captcha_img.save(captcha_path_for_outer)
                        self.logger.info(f"验证码图片已保存到 {captcha_path_for_outer}")
                    except Exception as e:
                        self.logger.warning(f"保存验证码图片失败: {e}")

                # 内层：对同一张验证码图片进行多次 OCR 识别
                for inner in range(inner_max):
                    self.logger.info(f"验证码识别（同一图片）尝试 {inner + 1}/{inner_max}，外层 {outer + 1}/{outer_max}")
                    captcha_text = self.recognize_captcha(captcha_img)

                    if not captcha_text:
                        self.logger.info("本次识别无有效结果，继续内层尝试")
                        continue

                    if len(captcha_text) != 4:
                        self.logger.info(f"识别结果长度不是4: {captcha_text!r}, len={len(captcha_text)}")
                        continue

                    # 在识别出有效的 4 位验证码后，将本轮外层截图重命名为“验证码结果.png”
                    if captcha_path_for_outer and os.path.exists(captcha_path_for_outer):
                        try:
                            safe_text = re.sub(r"[^A-Za-z0-9]", "", str(captcha_text).strip()).upper() or "UNKNOWN"
                            base_name = f"{safe_text}.png"
                            target_path = os.path.join(self.qr_pics_dir, base_name)
                            # 若已存在同名文件，则添加序号避免覆盖
                            if os.path.exists(target_path):
                                idx = 2
                                while os.path.exists(os.path.join(self.qr_pics_dir, f"{safe_text}_{idx}.png")):
                                    idx += 1
                                target_path = os.path.join(self.qr_pics_dir, f"{safe_text}_{idx}.png")
                            os.replace(captcha_path_for_outer, target_path)
                            self.logger.info(f"已将验证码图片重命名为: {target_path}")
                            # 更新路径，避免后续误用旧路径
                            captcha_path_for_outer = target_path
                        except Exception as e:
                            self.logger.warning(f"重命名验证码图片为识别结果时出错: {e}")

                    # 仅当结果为4个字符时，才填写并尝试登录
                    if not self.fill_captcha(captcha_text):
                        self.logger.warning("验证码填写失败，结束当前外层重试，准备重新加载页面")
                        break

                    self.logger.info("验证码填写完成，点击登录按钮...")
                    if not self.click_login_button():
                        self.logger.warning("点击登录按钮失败，结束当前外层重试，准备重新加载页面")
                        break

                    # 登录按钮已点击，检查是否存在无效验证码提示
                    time.sleep(2)
                    try:
                        # 统一使用 has_captcha_error，兼容中英文提示
                        if self.has_captcha_error():
                            self.logger.warning("检测到验证码错误提示，将重新加载页面获取新验证码")
                            # 结束当前外层，进入下一轮 outer（刷新页面由外层完成）
                            break
                        else:
                            self.logger.info("未检测到验证码错误提示，验证码可能通过")
                            return True
                    except Exception as e:
                        self.logger.warning(f"检测验证码错误状态时出错: {e}")
                        # 保守起见视为验证码已通过，等待后续登录成功判断
                        return True

                # 内层循环结束仍未成功登录，则继续下一轮外层重试（刷新页面并获取新的验证码）

            # 自动识别失败，优先判断当前环境是否允许手动输入
            import sys
            if not sys.stdin.isatty():
                # GitHub Actions 等非交互环境下，无法手动输入验证码，直接结束本次尝试
                self.logger.error("自动验证码识别失败，且当前为非交互环境，无法手动输入验证码，本次监控流程失败")
                return False

            # 仅在本地交互环境中提供手动输入选项
            self.logger.warning("自动验证码识别失败，请手动输入")
            captcha_img = last_captcha_img
            try:
                if not self.headless_mode and captcha_img:
                    captcha_img.show()
                    self.logger.info("验证码图片已显示，请查看")
            except Exception as e:
                self.logger.warning(f"无法显示验证码图片: {e}")
            manual_captcha = input("请手动输入验证码: ").strip()
            if manual_captcha:
                self.fill_login_form()
                if self.fill_captcha(manual_captcha):
                    if self.click_login_button():
                        time.sleep(2)
                        try:
                            if self.has_captcha_error():
                                self.logger.error("手动验证码也无效")
                                return False
                            else:
                                self.logger.info("手动验证码通过")
                                return True
                        except Exception as e:
                            self.logger.warning(f"检测验证码错误状态时出错: {e}")
                            return True
                    else:
                        self.logger.error("手动验证码填写后点击登录失败")
                        return False
                else:
                    self.logger.error("手动验证码填写失败")
                    return False
            else:
                self.logger.warning("未输入验证码")
                return False
        except Exception as e:
            self.logger.error(f"处理验证码时出错: {e}")
            return False
    
    def click_login_button(self):
        """点击登录按钮"""
        try:
            self.logger.info("查找并点击登录按钮...")
            login_button = None

            # 1) 优先按 id 查找（兼容 <a id="login_submit">）
            try:
                btn = self.driver.find_element(By.ID, "login_submit")
                if btn.is_displayed() and btn.is_enabled():
                    login_button = btn
                    self.logger.info("通过 ID=login_submit 找到登录按钮")
            except NoSuchElementException:
                pass

            # 2) 若未找到，再按你提供的 XPath 规则查找第一个匹配的 <a>
            if login_button is None:
                try:
                    btns = self.driver.find_elements(By.XPATH, "(//a[@id='login_submit'])[1]")
                    if btns:
                        btn = btns[0]
                        if btn.is_displayed() and btn.is_enabled():
                            login_button = btn
                            self.logger.info("通过 XPath (//a[@id='login_submit'])[1] 找到登录按钮")
                except Exception as e:
                    self.logger.warning(f"通过 XPath 查找登录按钮出错: {e}")

            # 3) 仍未找到则回退到旧的 CSS 选择器
            if login_button is None:
                try:
                    btn = self.driver.find_element(By.CSS_SELECTOR, "button.auth_login_btn.primary.full_width")
                    if btn.is_displayed() and btn.is_enabled():
                        login_button = btn
                        self.logger.info("通过旧 CSS 选择器找到登录按钮")
                except NoSuchElementException:
                    pass

            if login_button is None:
                self.logger.error("未找到登录按钮（ID/XPath/CSS 均未命中）")
                return False

            try:
                login_button.click()
                self.logger.info("已点击登录按钮")
                time.sleep(5)  # 等待登录处理
                return True
            except Exception as e:
                self.logger.error(f"点击登录按钮时发生异常: {e}")
                return False
            
        except Exception as e:
            self.logger.error(f"点击登录按钮时出错: {e}")
            return False

    def has_captcha_error(self):
        """检测当前页面是否存在与验证码相关的错误提示。

        兼容旧版中文提示（“无效的验证码”、“验证码错误”）和新版英文提示
        （例如 “Verification code error”、“Invalid verification code”）。
        """
        try:
            keywords_cn = ["无效的验证码", "验证码错误"]
            keywords_en = ["verification code error", "invalid verification code"]

            # 优先检查常见错误提示元素
            candidate_elements = []
            # 旧版统一认证错误提示元素（auto 脚本原有逻辑）
            try:
                candidate_elements.append(self.driver.find_element(By.ID, "msg1"))
            except NoSuchElementException:
                pass
            try:
                candidate_elements.append(self.driver.find_element(By.ID, "captchaErrorTip"))
            except NoSuchElementException:
                pass
            try:
                candidate_elements.append(self.driver.find_element(By.ID, "formErrorTip"))
            except NoSuchElementException:
                pass
            try:
                candidate_elements.extend(self.driver.find_elements(By.CLASS_NAME, "form-errorTip"))
            except Exception:
                pass

            for elem in candidate_elements:
                try:
                    if not elem:
                        continue
                    text = (elem.text or "") + " " + (elem.get_attribute("title") or "")
                    text_stripped = text.strip()
                    text_lower = text_stripped.lower()

                    if any(kw in text_stripped for kw in keywords_cn):
                        self.logger.info(f"检测到中文验证码错误提示: {text_stripped!r}")
                        return True
                    if any(kw in text_lower for kw in keywords_en):
                        self.logger.info(f"检测到英文验证码错误提示: {text_stripped!r}")
                        return True
                except Exception:
                    continue

            # 回退：在页面源码中搜索关键字
            try:
                source = self.driver.page_source
                if any(kw in source for kw in keywords_cn):
                    self.logger.info("在页面源码中检测到中文验证码错误提示")
                    return True
                lower_source = source.lower()
                if any(kw in lower_source for kw in keywords_en):
                    self.logger.info("在页面源码中检测到英文验证码错误提示")
                    return True
            except Exception:
                pass

            return False
        except Exception as e:
            self.logger.warning(f"检测验证码错误提示时出错: {e}")
            return False
    
    def wait_for_login_success(self):
        """等待登录成功"""
        try:
            self.logger.info("等待登录成功...")
            # 最多等待 15 秒，直到跳转到电费页面
            try:
                WebDriverWait(self.driver, 15).until(
                    lambda d: "epay.nju.edu.cn" in d.current_url.lower()
                    and "electric" in d.current_url.lower()
                )
            except Exception:
                # 未在预期时间内跳转到电费页面，检查当前 URL
                current_url = self.driver.current_url
                lower_url = current_url.lower()

                # 仍然停留在统一认证登录页，视为登录失败
                if "authserver.nju.edu.cn" in lower_url and "login" in lower_url:
                    self.logger.error(f"仍停留在统一认证登录页，当前 URL: {current_url}")
                    # 如果有验证码错误提示，也一并记录，方便排查
                    if self.has_captcha_error():
                        self.logger.error("登录失败可能由验证码错误导致")
                    return False

                # 其他情况：可能跳转到了中间页面，记录但仍尝试继续
                self.logger.info(f"页面已跳转到: {current_url}")
                return True

            # 成功跳转到电费页面
            self.logger.info(f"检测到已跳转到电费页面: {self.driver.current_url}")
            return True
                
        except Exception as e:
            self.logger.error(f"等待登录成功时出错: {e}")
            return False
    
    def click_recharge_button(self):
        """点击'去充值'按钮"""
        try:
            self.logger.info("查找'去充值'按钮...")
            recharge_button = None

            # 1) 优先按文本内容查找包含“去充值”的可点击元素
            try:
                candidates = self.driver.find_elements(By.XPATH, "//*[contains(text(),'去充值')]")
                visible = [el for el in candidates if el.is_displayed() and el.is_enabled()]
                if visible:
                    recharge_button = visible[0]
                    self.logger.info("通过文本包含 '去充值' 找到充值按钮")
            except Exception as e:
                self.logger.warning(f"通过文本查找'去充值'按钮出错: {e}")

            # 2) 若未找到，则回退到原先的 CSS 选择器 div.footer
            if recharge_button is None:
                try:
                    btn = self.driver.find_element(By.CSS_SELECTOR, "div.footer")
                    if btn.is_displayed() and btn.is_enabled():
                        recharge_button = btn
                        self.logger.info("通过 CSS 选择器 div.footer 找到充值按钮")
                except NoSuchElementException:
                    self.logger.warning("通过 CSS 选择器未找到'去充值'按钮 div.footer")

            if recharge_button is None:
                self.logger.error("未找到可点击的'去充值'按钮")
                return False

            try:
                recharge_button.click()
                self.logger.info("已点击充值按钮")
                time.sleep(3)
                return True
            except Exception as e:
                self.logger.error(f"点击充值按钮时发生异常: {e}")
                return False
            
        except Exception as e:
            self.logger.error(f"点击充值按钮时出错: {e}")
            return False
    
    def extract_remaining_electricity(self):
        """提取剩余电量信息"""
        try:
            self.logger.info("开始提取剩余电量信息...")
            try:
                page_source = self.driver.page_source
                # 在写入调试 HTML 前，简单脱敏持卡人姓名
                try:
                    page_source_sanitized = re.sub(
                        r"(持卡人姓名[：:].*?<i>)(.*?)(</i>)",
                        r"\1***\3",
                        page_source,
                        flags=re.S,
                    )
                except Exception:
                    page_source_sanitized = page_source

                debug_html_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'debug_page_source.html')
                with open(debug_html_path, "w", encoding="utf-8") as f:
                    f.write(page_source_sanitized)
                self.logger.info(f"页面源码已保存到 {debug_html_path}")
            except Exception as e:
                self.logger.warning(f"保存页面源码失败: {e}")
            
            # 方法1：使用精确的CSS选择器查找电量信息
            try:
                electricity_element = self.driver.find_element(By.CSS_SELECTOR, "span.fl")
                if (electricity_element):
                    text = electricity_element.text
                    self.logger.info("找到电量信息元素（已省略具体文本以保护隐私）")
                    
                    # 使用正则表达式提取数字
                    pattern = r'剩余电量[：:]\s*(\d+(?:\.\d+)?)\s*度'
                    match = re.search(pattern, text)
                    if match:
                        remaining_electricity = float(match.group(1))
                        self.logger.info(f"成功提取剩余电量: {remaining_electricity} 度")
                        return remaining_electricity
                    else:
                        self.logger.warning("未在元素中找到标准格式的电量信息")
                else:
                    self.logger.warning("未找到电量信息元素")
                    
            except NoSuchElementException:
                self.logger.warning("未找到电量信息元素，尝试其他方法...")
            
            # 方法2：查找包含电量的i标签
            try:
                electricity_i = self.driver.find_element(By.CSS_SELECTOR, "span.fl i")
                if electricity_i:
                    text = electricity_i.text
                    self.logger.info("找到 i 标签中的电量信息（已省略具体文本）")
                    
                    # 提取数字
                    pattern = r'(\d+(?:\.\d+)?)\s*度'
                    match = re.search(pattern, text)
                    if match:
                        remaining_electricity = float(match.group(1))
                        self.logger.info(f"从i标签中提取剩余电量: {remaining_electricity} 度")
                        return remaining_electricity
                    else:
                        self.logger.warning("i标签中未找到标准格式的电量信息")
                        
                self.logger.warning("未找到i标签中的电量信息")

            except NoSuchElementException:
                self.logger.warning("未找到i标签中的电量信息，尝试其他方法...")
            
            # 方法3：在页面源码中查找
            page_source = self.driver.page_source
            self.logger.info("在页面源码中查找电量信息...")
            
            # 查找包含电量的HTML结构
            patterns = [
                r'剩余电量[：:]\s*<i>(\d+(?:\.\d+)?)度</i>',  # 匹配HTML结构
                r'剩余电量[：:]\s*(\d+(?:\.\d+)?)\s*度',      # 匹配纯文本
                r'电量[：:]\s*(\d+(?:\.\d+)?)\s*度',          # 简化匹配
                r'<i>(\d+(?:\.\d+)?)度</i>'                  # 直接匹配i标签
            ]
            
            for pattern in patterns:
                match = re.search(pattern, page_source)
                if match:
                    remaining_electricity = float(match.group(1))
                    self.logger.info(f"从页面源码中提取到剩余电量: {remaining_electricity} 度")
                    return remaining_electricity
            
            # 方法4：查找所有包含"度"的元素
            try:
                elements_with_degree = self.driver.find_elements(By.XPATH, "//*[contains(text(), '度')]")
                for element in elements_with_degree:
                    text = element.text
                    self.logger.info("找到包含 '度' 的元素（已省略具体文本）")
                    
                    # 尝试提取数字
                    pattern = r'(\d+(?:\.\d+)?)\s*度'
                    match = re.search(pattern, text)
                    if match:
                        remaining_electricity = float(match.group(1))
                        self.logger.info(f"从元素中提取剩余电量: {remaining_electricity} 度")
                        return remaining_electricity
                        
            except Exception as e:
                self.logger.warning(f"查找包含'度'的元素时出错: {e}")
            
            self.logger.warning("未能提取到剩余电量信息")
            return None
            
        except Exception as e:
            self.logger.error(f"提取剩余电量时出错: {e}")
            return None
    
    def save_data(self, remaining_electricity):
        """保存数据到文件"""
        try:
            if remaining_electricity is None:
                self.logger.warning("没有电量数据可保存")
                return

            # 构造数据
            data = {
                # 数据时间使用北京时间并包含时区信息（如果可用）
                "timestamp": (datetime.now(BEIJING_TZ).replace(tzinfo=None).isoformat() if BEIJING_TZ else datetime.now().isoformat()),
                "remaining_electricity": remaining_electricity,
                "unit": "度"
            }

            # 保存为json
            json_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'electricity_data.json')
            with open(json_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")

            # 重新从json文件读取所有数据，生成csv（字段顺序为time,num,unit）
            import csv
            csv_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'electricity_data.csv')
            rows = []
            with open(json_path, "r", encoding="utf-8") as jf:
                for line in jf:
                    try:
                        item = json.loads(line)
                        rows.append({
                            "time": item.get("timestamp"),
                            "num": item.get("remaining_electricity"),
                            "unit": item.get("unit")
                        })
                    except Exception:
                        continue
            # 用 pandas 统一时间戳格式
            import pandas as pd
            df = pd.DataFrame(rows)
            # 统一解析为 Asia/Shanghai 时区（先解析，后去除时区）
            df["time"] = pd.to_datetime(df["time"], errors="coerce")
            # 确保在读取 CSV 文件后，时间列的时区信息被正确处理
            df['time'] = pd.to_datetime(df['time'], errors='coerce')
            if df['time'].dt.tz is not None:
                df['time'] = df['time'].dt.tz_localize(None)
            # 如果有时区，先转为 Asia/Shanghai，再去除时区
            if df["time"].dt.tz is None or str(df["time"].dt.tz) == "None":
                df["time"] = df["time"].dt.tz_localize("Asia/Shanghai", ambiguous='NaT', nonexistent='NaT')
            else:
                df["time"] = df["time"].dt.tz_convert("Asia/Shanghai")
            # 去除时区信息
            df["time"] = df["time"].dt.tz_localize(None)
            # 格式化为 "YYYY-MM-DDTHH:MM:SS.ssssss"（无时区）
            df["time"] = df["time"].dt.strftime('%Y-%m-%dT%H:%M:%S.%f')
            # 写回 CSV
            df.to_csv(csv_path, index=False, header=True, columns=["time", "num", "unit"])

            self.logger.info(f"数据已保存: {remaining_electricity} 度")

            # 生成网页版类似的曲线图并保存为PNG
            try:
                
                csv_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'electricity_data.csv')
                png_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'electricity_trend.png')
                df = pd.read_csv(csv_path)
                try:
                    df['time'] = pd.to_datetime(df['time'], format='ISO8601')
                except Exception:
                    df['time'] = pd.to_datetime(df['time'], errors='coerce')
                df_sorted = df.sort_values('time')

                # 确保所有时间点的时区信息被正确移除
                df_sorted['time'] = pd.to_datetime(df_sorted['time'], errors='coerce')
                if df_sorted['time'].dt.tz is not None:
                    df_sorted['time'] = df_sorted['time'].dt.tz_localize(None)

                # 设置深色科技感风格
                plt.style.use('dark_background')
                fig, ax = plt.subplots(figsize=(9, 4), dpi=200)
                fig.patch.set_facecolor('#141e30')
                ax.set_facecolor('#0a1428')

                # 线条和点的颜色
                line_color = '#1de9b6'
                marker_color = '#00eaff'
                grid_color = 'rgba(29,233,182,0.15)'
                grid_color = (29/255, 233/255, 182/255, 0.15)
                font_color = '#b2e6ff'
                title_color = '#00eaff'

                # 绘制曲线和点
                ax.plot(df_sorted['time'], df_sorted['num'],
                        color=line_color, linewidth=2.5, marker='o', markersize=6,
                        markerfacecolor=marker_color, markeredgewidth=2, markeredgecolor=marker_color, zorder=3)

                # 设置标题和标签
                ax.set_title('电量变化曲线', fontsize=18, color=title_color, pad=18, fontweight='bold', fontname='Microsoft YaHei')
                ax.set_xlabel('时间', fontsize=13, color=font_color, labelpad=10, fontname='Microsoft YaHei')
                ax.set_ylabel('剩余电量 (度)', fontsize=13, color=font_color, labelpad=10, fontname='Microsoft YaHei')

                # 坐标轴刻度
                ax.tick_params(axis='x', colors=font_color, labelsize=10, rotation=30)
                ax.tick_params(axis='y', colors=font_color, labelsize=10)
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
                ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))
                ax.yaxis.set_major_locator(MaxNLocator(integer=True))

                # 虚线网格
                ax.grid(True, which='major', axis='both', linestyle='--', linewidth=1, color=grid_color, alpha=1)

                # 去除顶部和右侧边框
                ax.spines['top'].set_visible(False)
                ax.spines['right'].set_visible(False)
                for spine in ['bottom', 'left']:
                    ax.spines[spine].set_color(font_color)
                    ax.spines[spine].set_linewidth(1.2)

                # 使用初始化时配置的字体回退列表
                # 自动检测当前系统可用字体，优先使用已安装的中文字体
                import matplotlib.font_manager as fm
                preferred_fonts = [
                    'Microsoft YaHei',
                    'Segoe UI',
                    'Arial Unicode MS',
                    'Noto Sans CJK SC',
                    'Noto Sans CJK JP',
                    'Noto Sans',
                    'WenQuanYi Micro Hei',
                    'WenQuanYi Zen Hei',
                    'SimHei',
                    'STHeiti',
                    'Heiti SC',
                    'DejaVu Sans',
                    'Arial'
                ]
                for font in preferred_fonts:
                    try:
                        fpath = fm.findfont(font, fallback_to_default=False)
                        if fpath and os.path.exists(fpath):
                            ax.set_title(ax.get_title(), fontname=font)
                            ax.set_xlabel(ax.get_xlabel(), fontname=font)
                            ax.set_ylabel(ax.get_ylabel(), fontname=font)
                            break
                    except Exception:
                        continue

                # 图例（可选）
                # ax.legend(['剩余电量'], loc='upper right', fontsize=11, facecolor='#141e30', edgecolor='none', labelcolor=font_color)

                # 调整边距
                plt.tight_layout(rect=[0, 0, 1, 0.97])
                plt.savefig(png_path, facecolor=fig.get_facecolor(), bbox_inches='tight')
                plt.close(fig)
                self.logger.info(f"电量变化曲线图已保存到: {png_path}")
            except Exception as e:
                self.logger.warning(f"生成电量曲线图PNG失败: {e}")

            # 生成最近20次电量变化的曲线图并保存为PNG
            self.generate_recent_20_changes_plot(df_sorted)

        except Exception as e:
            self.logger.error(f"保存数据时出错: {e}")

    def generate_recent_20_changes_plot(self, df_sorted):
        """Generate and save the recent 20 changes plot as a PNG image."""
        try:
            # 设置深色科技感风格
            plt.style.use('dark_background')
            fig, ax = plt.subplots(figsize=(9, 4), dpi=200)
            fig.patch.set_facecolor('#141e30')
            ax.set_facecolor('#0a1428')

            # 线条和点的颜色
            line_color = '#ff4500'
            marker_color = '#ff6347'
            grid_color = (255/255, 69/255, 0/255, 0.15)
            font_color = '#ff6347'
            title_color = '#ff4500'

            # 获取最近20次数据
            recent_20 = df_sorted.tail(20)

            # 确保最近20次数据的时间列时区信息被正确移除
            recent_20['time'] = pd.to_datetime(recent_20['time'], errors='coerce')
            if recent_20['time'].dt.tz is not None:
                recent_20['time'] = recent_20['time'].dt.tz_localize(None)

            # 绘制曲线和点
            ax.plot(recent_20['time'], recent_20['num'],
                    color=line_color, linewidth=2.5, marker='o', markersize=6,
                    markerfacecolor=marker_color, markeredgewidth=2, markeredgecolor=marker_color, zorder=3)

            # 设置标题和标签
            ax.set_title('最近20次电量变化曲线', fontsize=18, color=title_color, pad=18, fontweight='bold', fontname='Microsoft YaHei')
            ax.set_xlabel('时间', fontsize=13, color=font_color, labelpad=10, fontname='Microsoft YaHei')
            ax.set_ylabel('剩余电量 (度)', fontsize=13, color=font_color, labelpad=10, fontname='Microsoft YaHei')

            # 坐标轴刻度
            ax.tick_params(axis='x', colors=font_color, labelsize=10, rotation=30)
            ax.tick_params(axis='y', colors=font_color, labelsize=10)
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))
            ax.yaxis.set_major_locator(MaxNLocator(integer=True))

            # 虚线网格
            ax.grid(True, which='major', axis='both', linestyle='--', linewidth=1, color=grid_color, alpha=1)

            # 去除顶部和右侧边框
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            for spine in ['bottom', 'left']:
                ax.spines[spine].set_color(font_color)
                ax.spines[spine].set_linewidth(1.2)

            # 使用初始化时配置的字体回退列表
            # 自动检测当前系统可用字体，优先使用已安装的中文字体
            import matplotlib.font_manager as fm
            preferred_fonts = [
                'Microsoft YaHei',
                'Segoe UI',
                'Arial Unicode MS',
                'Noto Sans CJK SC',
                'Noto Sans CJK JP',
                'Noto Sans',
                'WenQuanYi Micro Hei',
                'WenQuanYi Zen Hei',
                'SimHei',
                'STHeiti',
                'Heiti SC',
                'DejaVu Sans',
                'Arial'
            ]
            for font in preferred_fonts:
                try:
                    fpath = fm.findfont(font, fallback_to_default=False)
                    if fpath and os.path.exists(fpath):
                        ax.set_title(ax.get_title(), fontname=font)
                        ax.set_xlabel(ax.get_xlabel(), fontname=font)
                        ax.set_ylabel(ax.get_ylabel(), fontname=font)
                        break
                except Exception:
                    continue

            # 调整边距
            plt.tight_layout(rect=[0, 0, 1, 0.97])

            # 保存图片
            output_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'recent_20_changes.png')
            plt.savefig(output_path, facecolor=fig.get_facecolor(), bbox_inches='tight')
            plt.close(fig)
            self.logger.info(f"最近20次电量变化曲线图已保存到: {output_path}")
        except Exception as e:
            self.logger.warning(f"生成最近20次电量变化曲线图PNG失败: {e}")
    
    def run(self):
        """运行监控流程"""
        try:
            self.logger.info("开始南京大学电费监控流程（自动无头模式）")

            # 初始化并清空本次 workflow 运行的验证码图片目录
            self.init_qr_pics_dir()
            
            # 1. 获取登录凭据
            self.get_user_credentials()
            
            # 2. 打开页面
            self.logger.info(f"正在打开页面: {self.url}")
            self.driver.get(self.url)
            time.sleep(3)
            
            # 3. 等待登录表单加载
            if not self.wait_for_login_form():
                self.logger.error("登录表单加载失败")
                return False
            
            # 4. 填写登录表单
            if not self.fill_login_form():
                self.logger.error("填写登录表单失败")
                return False
            
            # 5. 处理验证码（内部负责在有/无验证码场景下点击登录按钮）
            if not self.handle_captcha():
                self.logger.error("验证码处理或登录过程失败")
                return False
            
            # 7. 等待登录成功
            if not self.wait_for_login_success():
                self.logger.error("登录失败")
                return False
            
            # 8. 点击充值按钮
            if not self.click_recharge_button():
                self.logger.warning("点击充值按钮失败，尝试直接提取数据")
            
            # 9. 提取剩余电量
            remaining_electricity = self.extract_remaining_electricity()

            # 如果未能成功提取电量，视为本次流程失败（可能是验证码/登录异常导致未进入目标页面）
            if remaining_electricity is None:
                self.logger.error("提取剩余电量失败，认为本次监控流程未成功，将交由上层重试")
                return False

            # 10. 保存数据
            self.save_data(remaining_electricity)
            
            self.logger.info("监控流程完成")
            return True
            
        except Exception as e:
            self.logger.error(f"监控流程出错: {e}")
            return False
        
        finally:
            if self.driver:
                self.driver.quit()

def main():
    """主函数"""
    import sys
    
    # 在 workflow 环境中使用 config_workflow.json（由 workflow 可注入 secrets）
    config_file = "config_workflow.json"
    if len(sys.argv) > 1:
        config_file = sys.argv[1]
    
    monitor = NJUElectricMonitor(config_file)
    try:
        monitor.run()
    except KeyboardInterrupt:
        print("\n用户中断程序")
    except Exception as e:
        print(f"程序运行出错: {e}")

if __name__ == "__main__":
    main()