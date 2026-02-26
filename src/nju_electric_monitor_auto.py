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
    def __init__(self, config_file="config.json"):
        """初始化监控器"""
        self.url = "https://epay.nju.edu.cn/epay/h5/nju/electric/index"
        self.config_file = config_file
        self.config = self.load_config()
        self.username = self.config.get("username", "")
        self.password = self.config.get("password", "")
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
        now = datetime.now()
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
        
    def load_config(self):
        """加载配置文件"""
        try:
            config_path = os.path.join(os.path.dirname(__file__), '..', 'config.json')
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            else:
                # 创建默认配置文件
                default_config = {
                    "username": "",
                    "password": "",
                    "auto_login": True,
                    "headless_mode": True,
                    "captcha_retry_count": 3,
                    "log_level": "INFO"
                }
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, indent=4, ensure_ascii=False)
                return default_config
        except Exception as e:
            print(f"加载配置文件失败: {e}")
            return {}
        
    def save_config(self):
        """保存配置文件"""
        try:
            self.config["username"] = self.username
            self.config["password"] = self.password
            config_path = os.path.join(os.path.dirname(__file__), '..', 'config.json')
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"保存配置文件失败: {e}")
        
    def setup_driver(self):
        """设置Chrome浏览器驱动"""
        chrome_options = Options()
        if self.headless_mode:
            chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-plugins")
        try:
            chromedriver_path = os.path.join(os.path.dirname(__file__), '..', 'chromedriver-win64', 'chromedriver.exe')
            if not os.path.exists(chromedriver_path):
                raise FileNotFoundError(f"本地ChromeDriver不存在: {chromedriver_path}，请确保chromedriver-win64目录存在并包含chromedriver.exe")
            service = Service(chromedriver_path)
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            self.wait = WebDriverWait(self.driver, 20)
            self.logger.info(f"使用本地ChromeDriver: {chromedriver_path}")
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
                download_enabled=False
            )
            self.logger.info("OCR识别器初始化成功")
        except Exception as e:
            self.logger.error(f"OCR识别器初始化失败: {e}")
            if "ANTIALIAS" in str(e):
                self.logger.error("PIL兼容性问题，请确保Pillow版本兼容")
            elif "CUDA" in str(e):
                self.logger.error("GPU相关问题，尝试使用CPU模式")
            raise
    
    def get_user_credentials(self):
        """获取用户登录凭据"""
        if not self.username:
            self.username = input("请输入用户名: ").strip()
        if not self.password:
            self.password = getpass.getpass("请输入密码: ")
        
        # 询问是否保存凭据
        if not self.config.get("username") or not self.config.get("password"):
            save_credentials = input("是否保存登录凭据到配置文件？(y/n): ").strip().lower()
            if save_credentials == 'y':
                self.save_config()
                self.logger.info("登录凭据已保存到配置文件")
        
        self.logger.info("已获取登录凭据")
    
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
            # 尝试使用主图和替代图的多结果集合，并优先选择长度为4的结果
            candidates = []

            def collect_from_results(results, tag='easyocr'):
                for (bbox, text, prob) in results:
                    text_clean = re.sub(r'[^A-Za-z0-9]', '', text.strip())
                    if text_clean:
                        candidates.append({'engine': tag, 'text': text_clean, 'prob': float(prob) if prob is not None else 0.0, 'raw': text})

            # 主图识别
            try:
                results = self.ocr_reader.readtext(img_array)
                collect_from_results(results, tag='easyocr')
            except Exception as e:
                self.logger.warning(f"主图 easyocr 识别失败: {e}")

            # 备用图像识别
            self.logger.info("尝试不同的图像处理参数...")
            alternative_images = self.generate_alternative_images(captcha_img)
            for i, alt_img in enumerate(alternative_images):
                try:
                    alt_arr = np.array(alt_img)
                    results = self.ocr_reader.readtext(alt_arr)
                    collect_from_results(results, tag=f'easyocr_alt{i+1}')
                except Exception as e:
                    self.logger.warning(f"alt{i+1} easyocr 识别失败: {e}")

            # 选择最优结果：优先长度为4的候选
            candidates = [c for c in candidates if c.get('text')]
            if candidates:
                len4 = [c for c in candidates if len(c.get('text','')) == 4]
                if len4:
                    best = max(len4, key=lambda x: x.get('prob', 0.0))
                    self.logger.info(f"最终验证码识别结果: {best['text']} (engine={best['engine']}, prob={best['prob']})")
                    return best['text']
                else:
                    voted = self.char_level_vote(candidates, target_len=4)
                    if voted:
                        self.logger.info(f"投票合成验证码结果: {voted}")
                        return voted
                    # 否则取置信度最高的
                    best = max(candidates, key=lambda x: x.get('prob', 0.0))
                    self.logger.info(f"使用置信度最高的候选: {best['text']} (engine={best['engine']}, prob={best['prob']})")
                    return best['text']

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
                "timestamp": datetime.now().isoformat(),
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

                # 设置字体（优先微软雅黑）
                try:
                    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'Segoe UI', 'Arial Unicode MS']
                except Exception:
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

            # 设置字体（优先微软雅黑）
            try:
                plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'Segoe UI', 'Arial Unicode MS']
            except Exception:
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
    
    config_file = "config.json"
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