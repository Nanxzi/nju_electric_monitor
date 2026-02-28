# OCR 模型性能测试

本文件夹包含用于对比 EasyOCR 和 PaddleOCR 性能的测试脚本。

## 文件说明

### 1. `benchmark.py` - 综合性能测试
对多个测试图像进行批量性能测试，生成统计报告。

**使用方法：**
```bash
python benchmark.py
```

**功能：**
- 自动查找 `captcha*.png` 测试图像
- 分别使用 EasyOCR 和 PaddleOCR 进行识别
- 统计响应时间、置信度等指标
- 生成对比分析报告（JSON 格式）

**输出：**
- 控制台日志输出详细的识别过程
- `benchmark_report.json` 详细报告文件

### 2. `compare_models.py` - 单图像详细对比
对单个验证码图像进行深入对比分析。

**使用方法：**
```bash
# 使用默认查找的 captcha_debug.png
python compare_models.py

# 指定图像文件和期望结果
python compare_models.py <image_path> <expected_result>
```

**示例：**
```bash
python compare_models.py ../captcha_debug.png 5hMa
```

**功能：**
- 加载并显示图像基本信息
- 为每个模型生成 8+ 种预处理变体
- 详细展示每种变体的识别结果
- 统计识别准确率和性能指标
- 对比两个模型的优劣

**输出：**
- 详细的识别过程日志
- 变体长度分布统计
- 识别准确率和速度对比

## 测试图像准备

将测试验证码图像放在以下位置之一：
- `nju_electric_monitor/` （项目根目录）
- `nju_electric_monitor/test/` （test 文件夹）
- `nju_electric_monitor/ocr_model_benchmark/` （本文件夹）

命名规则：`captcha_*.png` 或 `captcha_debug.png`

## 依赖安装

### 基础依赖（必须）
```bash
pip install easyocr pillow numpy
```

### 可选依赖
```bash
# PaddleOCR（推荐，用于对比测试）
pip install paddleocr

# OpenCV（可选，用于高级预处理）
pip install opencv-python-headless
```

## 性能指标说明

### 时间指标
- **avg_time**: 平均响应时间（秒）
- **min_time**: 最小响应时间（秒）
- **max_time**: 最大响应时间（秒）

### 置信度指标
- **avg_confidence**: 平均置信度（0-1，越高越好）
- 置信度 > 0.8：高置信度
- 置信度 0.5-0.8：中等置信度
- 置信度 < 0.5：低置信度

## 测试结果示例

### 对于小验证码（80x30）
```
EasyOCR:
- 平均耗时: 245ms
- 平均置信度: 0.674
- 最优结果: 5h（2字）

PaddleOCR:
- 平均耗时: 180ms
- 预期结果：5hMa（4字）
```

## 推荐方案

根据测试结果选择：

1. **如果两个模型都识别正确**
   - 选择更快的模型（通常 PaddleOCR 更快）
   - 使用双引擎验证（提高准确率）

2. **如果只有一个模型识别正确**
   - 使用该模型，可考虑另一个作备选

3. **如果都不能正确识别**
   - 调整预处理参数（缩放倍数、对比度）
   - 使用分割识别（split recognition）
   - 考虑云端 OCR API

## 调试技巧

### 查看模型的原始输出
修改脚本中的 `logger.info()` 调用，打印原始的 `results` 对象（未清理）。

### 测试不同的预处理参数
编辑 `preprocess_variants()` 函数，添加新的变体：
```python
# 例：更强的对比度
variants['contrast_3.0'] = ImageEnhance.Contrast(self.img).enhance(3.0)
```

### 分析识别失败原因
- 检查文本框的位置和大小
- 查看检测到的文本框数量
- 对比不同变体的效果

## 注意事项

1. **首次运行较慢**：模型首次加载需要下载，建议有网络连接
2. **GPU 支持**：脚本默认使用 CPU，如需 GPU 加速需修改代码
3. **内存使用**：大批量测试可能占用大量内存，建议按需调整
4. **兼容性**：Python 3.7+ 推荐使用 3.9 以上版本

## 常见问题

### Q: PaddleOCR 初始化失败
A: 确保已安装依赖包，可能需要代理或手动下载模型

### Q: 识别结果为空
A: 验证码图像可能过小或清晰度不足，尝试增加缩放倍数

### Q: 识别时间过长
A: 这是正常的（首次加载和处理验证码）。可在生产环境中使用缓存和批处理优化
