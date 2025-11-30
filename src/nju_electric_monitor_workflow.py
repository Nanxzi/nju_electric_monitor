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
import easyocr
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
        self.captcha_retry_count = self.config.get("captcha_retry_count", 5)
        self.captcha_confidence_threshold = self.config.get("captcha_confidence_threshold", 0.3)
        self.save_captcha_images = self.config.get("save_captcha_images", True)
        self.driver = None
        self.wait = None
        self.ocr_reader = None
        
        # 设置日志级别
        log_level = getattr(logging, self.config.get("log_level", "INFO"))
        self.setup_logging(log_level)
        
        self.setup_driver()
        self.setup_ocr()
        
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
        """设置OCR识别器"""
        try:
            model_dir = os.path.join(os.path.dirname(__file__), '..', 'models', 'ocr_models')
            self.ocr_reader = easyocr.Reader(
                ['ch_sim', 'en'],
                gpu=False,
                model_storage_directory=model_dir,
                download_enabled=True
            )
            self.logger.info("OCR识别器初始化成功")
        except Exception as e:
            self.logger.error(f"OCR识别器初始化失败: {e}")
            if "ANTIALIAS" in str(e):
                self.logger.error("PIL兼容性问题，请确保Pillow版本兼容")
            elif "CUDA" in str(e):
                self.logger.error("GPU相关问题，尝试使用CPU模式")
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
                captcha_img = self.driver.find_element(By.ID, "captchaImg")
                if captcha_img.is_displayed():
                    self.logger.info("找到验证码图片，开始截图...")
                    # 直接截取页面元素图片
                    img_bytes = captcha_img.screenshot_as_png
                    img = Image.open(io.BytesIO(img_bytes))
                    self.logger.info("验证码图片截取成功")
                    return img
                else:
                    self.logger.warning("验证码图片不可见")
                    return None
            except NoSuchElementException:
                self.logger.warning("未找到验证码图片")
                return None
        except Exception as e:
            self.logger.error(f"捕获验证码图片时出错: {e}")
            return None
    
    def recognize_captcha(self, captcha_img):
        """识别验证码"""
        try:
            if not captcha_img:
                return None
            
            self.logger.info("开始识别验证码...")
            
            # 图像预处理
            processed_img = self.preprocess_captcha_image(captcha_img)
            
            # 将PIL Image转换为numpy数组
            import numpy as np
            img_array = np.array(processed_img)
            
            # 方法1：使用OCR识别验证码
            results = self.ocr_reader.readtext(img_array)
            
            if results:
                # 提取识别结果
                captcha_text = ""
                for (bbox, text, prob) in results:
                    if prob > self.captcha_confidence_threshold:  # 降低置信度阈值
                        captcha_text += text.strip()
                
                # 清理识别结果，只保留字母和数字
                captcha_text = re.sub(r'[^a-zA-Z0-9]', '', captcha_text)
                
                if captcha_text:
                    self.logger.info(f"验证码识别结果: {captcha_text}")
                    return captcha_text
                else:
                    self.logger.warning("验证码识别结果为空")
            
            # 方法2：尝试不同的图像处理参数
            self.logger.info("尝试不同的图像处理参数...")
            alternative_images = self.generate_alternative_images(captcha_img)
            
            for i, alt_img in enumerate(alternative_images):
                try:
                    # 转换为numpy数组
                    alt_img_array = np.array(alt_img)
                    results = self.ocr_reader.readtext(alt_img_array)
                    if results:
                        captcha_text = ""
                        for (bbox, text, prob) in results:
                            if prob > self.captcha_confidence_threshold:  # 进一步降低阈值
                                captcha_text += text.strip()
                        
                        captcha_text = re.sub(r'[^a-zA-Z0-9]', '', captcha_text)
                        if len(captcha_text) == 4:
                            self.logger.info(f"方法{i+2}识别结果: {captcha_text}")
                            return captcha_text
                        else:
                            self.logger.info(f"方法{i+2}识别结果长度不为4，自动重试。实际结果: {captcha_text}")
                except Exception as e:
                    self.logger.warning(f"方法{i+2}识别失败: {e}")
            
            self.logger.warning("所有识别方法都失败了")
            return None
                
        except Exception as e:
            self.logger.error(f"识别验证码时出错: {e}")
            return None
    
    def preprocess_captcha_image(self, img):
        """预处理验证码图像"""
        try:
            # 转换为RGB模式
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # 调整图像大小 - 使用兼容的重采样方法
            try:
                img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
            except AttributeError:
                # 如果Resampling不可用，使用ANTIALIAS
                img = img.resize((img.width * 2, img.height * 2), Image.ANTIALIAS)
            
            # 转换为灰度图
            gray_img = img.convert('L')
            
            # 二值化处理
            threshold = 128
            binary_img = gray_img.point(lambda x: 0 if x < threshold else 255, '1')
            
            # 确保二值化后的图像为 uint8 类型
            binary_img = binary_img.convert('L')
            binary_img = binary_img.point(lambda x: 255 if x > 0 else 0, '1')
            binary_img = binary_img.convert('L')
            
            # 降噪处理
            from PIL import ImageFilter
            denoised_img = binary_img.filter(ImageFilter.MedianFilter(size=3))
            
            return denoised_img
            
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
            
        except Exception as e:
            self.logger.warning(f"生成替代图像失败: {e}")
        
        return alternative_images
    
    def fill_captcha(self, captcha_text):
        """填写验证码"""
        try:
            if not captcha_text:
                self.logger.warning("没有验证码文本可填写")
                return False
            
            self.logger.info("查找验证码输入框...")
            
            # 使用精确的ID选择器
            try:
                captcha_input = self.driver.find_element(By.ID, "captchaResponse")
                captcha_input.clear()
                captcha_input.send_keys(captcha_text)
                self.logger.info("验证码填写完成")
                return True
            except NoSuchElementException:
                self.logger.error("未找到验证码输入框")
                return False
                
        except Exception as e:
            self.logger.error(f"填写验证码时出错: {e}")
            return False
    
    def handle_captcha(self):
        """处理验证码（支持多次尝试无效验证码）"""
        try:
            self.logger.info("检查是否有验证码...")
            captcha_img = self.capture_captcha_image()
            max_attempts = self.captcha_retry_count
            for attempt in range(max_attempts):
                self.fill_login_form()
                if captcha_img:
                    # 保存验证码图片用于调试
                    if self.save_captcha_images:
                        try:
                            captcha_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'captcha_debug.png')
                            captcha_img.save(captcha_path)
                            self.logger.info(f"验证码图片已保存到 {captcha_path}")
                        except Exception as e:
                            self.logger.warning(f"保存验证码图片失败: {e}")
                    self.logger.info(f"验证码识别尝试 {attempt + 1}/{max_attempts}")
                    captcha_text = self.recognize_captcha(captcha_img)
                    if captcha_text:
                        if self.fill_captcha(captcha_text):
                            self.logger.info(f"验证码填写完成，点击登录按钮...")
                            if self.click_login_button():
                                # 检查是否出现无效验证码提示
                                time.sleep(2)
                                try:
                                    error_elem = self.driver.find_element(By.ID, "msg1")
                                    if error_elem.is_displayed() and "无效的验证码" in error_elem.text:
                                        self.logger.warning("检测到无效的验证码提示，准备重试...")
                                        # 重新获取验证码图片
                                        captcha_img = self.capture_captcha_image()
                                        continue
                                except NoSuchElementException:
                                    self.logger.info("未检测到无效验证码提示，验证码通过")
                                    return True
                                except Exception as e:
                                    self.logger.warning(f"检测验证码错误元素时出错: {e}")
                                    return True
                            else:
                                self.logger.warning("点击登录按钮失败")
                        else:
                            self.logger.warning("验证码填写失败")
                    else:
                        self.logger.warning(f"验证码识别失败，尝试 {attempt + 1}")
                else:
                    self.logger.info("未检测到验证码图片")
                    return True
            # 自动识别失败，提供手动输入选项
            self.logger.warning("自动验证码识别失败，请手动输入")
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
                            error_elem = self.driver.find_element(By.ID, "msg1")
                            if error_elem.is_displayed() and "无效的验证码" in error_elem.text:
                                self.logger.error("手动验证码也无效")
                                return False
                        except NoSuchElementException:
                            self.logger.info("手动验证码通过")
                            return True
                        except Exception as e:
                            self.logger.warning(f"检测验证码错误元素时出错: {e}")
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
            
            # 使用精确的CSS选择器
            try:
                login_button = self.driver.find_element(By.CSS_SELECTOR, "button.auth_login_btn.primary.full_width")
                if login_button.is_displayed() and login_button.is_enabled():
                    login_button.click()
                    self.logger.info("已点击登录按钮")
                    time.sleep(5)  # 等待登录处理
                    return True
                else:
                    self.logger.warning("登录按钮不可见或不可点击")
                    return False
            except NoSuchElementException:
                self.logger.error("未找到登录按钮")
                return False
            
        except Exception as e:
            self.logger.error(f"点击登录按钮时出错: {e}")
            return False
    
    def wait_for_login_success(self):
        """等待登录成功"""
        try:
            self.logger.info("等待登录成功...")
            time.sleep(5)  # 等待页面跳转
            
            # 检查是否还在登录页面
            current_url = self.driver.current_url
            if "login" in current_url.lower() or "index" in current_url.lower():
                self.logger.info("登录可能成功，继续下一步...")
                return True
            else:
                self.logger.info(f"页面已跳转到: {current_url}")
                return True
                
        except Exception as e:
            self.logger.error(f"等待登录成功时出错: {e}")
            return False
    
    def click_recharge_button(self):
        """点击'去充值'按钮"""
        try:
            self.logger.info("查找'去充值'按钮...")
            
            # 使用精确的CSS选择器
            try:
                recharge_button = self.driver.find_element(By.CSS_SELECTOR, "div.footer")
                if recharge_button.is_displayed() and recharge_button.is_enabled():
                    recharge_button.click()
                    self.logger.info("已点击充值按钮")
                    time.sleep(3)
                    return True
                else:
                    self.logger.warning("充值按钮不可见或不可点击")
                    return False
            except NoSuchElementException:
                self.logger.error("未找到'去充值'按钮")
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
                debug_html_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'debug_page_source.html')
                with open(debug_html_path, "w", encoding="utf-8") as f:
                    f.write(page_source)
                self.logger.info(f"页面源码已保存到 {debug_html_path}")
            except Exception as e:
                self.logger.warning(f"保存页面源码失败: {e}")
            
            # 方法1：使用精确的CSS选择器查找电量信息
            try:
                electricity_element = self.driver.find_element(By.CSS_SELECTOR, "span.fl")
                if (electricity_element):
                    text = electricity_element.text
                    self.logger.info(f"找到电量信息元素: {text}")
                    
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
                    self.logger.info(f"找到i标签中的电量信息: {text}")
                    
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
                    self.logger.info(f"找到包含'度'的元素: {text}")
                    
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
                "timestamp": (datetime.now(BEIJING_TZ).isoformat() if BEIJING_TZ else datetime.now().isoformat()),
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
            with open(json_path, "r", encoding="utf-8") as jf, \
                 open(csv_path, "w", newline='', encoding="utf-8") as cf:
                writer = csv.DictWriter(cf, fieldnames=["time", "num", "unit"])
                writer.writeheader()
                for line in jf:
                    try:
                        item = json.loads(line)
                        writer.writerow({
                            "time": item.get("timestamp"),
                            "num": item.get("remaining_electricity"),
                            "unit": item.get("unit")
                        })
                    except Exception:
                        continue

            self.logger.info(f"数据已保存: {remaining_electricity} 度")

            # 生成网页版类似的曲线图并保存为PNG
            try:
                
                csv_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'electricity_data.csv')
                png_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'electricity_trend.png')
                df = pd.read_csv(csv_path)
                df['time'] = pd.to_datetime(df['time'])
                df_sorted = df.sort_values('time')

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
                pass

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
            pass

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
            
            # 5. 处理验证码
            if not self.handle_captcha():
                self.logger.warning("验证码处理失败，但继续尝试登录")
            
            # 6. 点击登录按钮
            if not self.click_login_button():
                self.logger.error("点击登录按钮失败")
                # return False
            
            # 7. 等待登录成功
            if not self.wait_for_login_success():
                self.logger.error("登录失败")
                return False
            
            # 8. 点击充值按钮
            if not self.click_recharge_button():
                self.logger.warning("点击充值按钮失败，尝试直接提取数据")
            
            # 9. 提取剩余电量
            remaining_electricity = self.extract_remaining_electricity()
            
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