#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
轻量 EasyOCR 简化识别脚本
- 生成几种简单预处理变体
- 对整图和按等宽分割（4片）分别识别
- 基于长度与置信度选择最优结果
"""
import sys
import os
import time
import logging
from pathlib import Path
from PIL import Image, ImageFilter, ImageEnhance, ImageOps, ImageChops
import itertools
import csv
import numpy as np

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent

try:
    import easyocr
except Exception as e:
    logger.error('EasyOCR 未安装：%s', e)
    raise

# Pillow 兼容性补丁（部分库仍使用 Image.ANTIALIAS）
try:
    if not hasattr(Image, 'ANTIALIAS'):
        Image.ANTIALIAS = Image.Resampling.LANCZOS
except Exception:
    pass


def gen_variants(img):
    variants = {}
    # legacy strict binary first (high-priority variant)
    try:
        def legacy_binarize_local(im):
            g = im.convert('L')
            try:
                g = g.resize((g.width*2, g.height*2), Image.Resampling.LANCZOS)
            except Exception:
                g = g.resize((g.width*2, g.height*2))
            bw = g.point(lambda x: 0 if x < 128 else 255, '1')
            bw = bw.convert('L')
            try:
                bw = bw.filter(ImageFilter.MedianFilter(size=3))
            except Exception:
                pass
            return bw
        variants['legacy_binary'] = legacy_binarize_local(img)
    except Exception:
        pass
    variants['original'] = img
    variants['grayscale'] = img.convert('L')
    # equalized
    try:
        variants['equalize'] = ImageOps.equalize(img.convert('L'))
    except Exception:
        pass
    # enlargements
    for s in [2,4,6,8]:
        try:
            variants[f'{s}x'] = img.resize((img.width*s, img.height*s), Image.Resampling.LANCZOS)
        except Exception:
            variants[f'{s}x'] = img.resize((img.width*s, img.height*s))
    # median denoise
    try:
        variants['median'] = img.filter(ImageFilter.MedianFilter(size=3))
    except Exception:
        pass
    # sharpen
    variants['sharpen'] = img.filter(ImageFilter.SHARPEN)
    # contrast
    try:
        enhancer = ImageEnhance.Contrast(img)
        variants['contrast_1.6'] = enhancer.enhance(1.6)
    except Exception:
        pass
    # adaptive/local threshold variant (approx via box blur)
    try:
        gray = img.convert('L')
        blurred = gray.filter(ImageFilter.BoxBlur(2))
        arr = np.array(gray)
        b = np.array(blurred)
        th = (b - 8).clip(0,255)
        bw = (arr < th).astype(np.uint8) * 255
        variants['adaptive_thresh'] = Image.fromarray(bw).convert('L')
    except Exception:
        pass
    return variants


def run_experiments_qr(folder_path, reader=None, out_csv=None):
    p = Path(folder_path)
    if not p.exists() or not p.is_dir():
        print('QR 文件夹不存在：', folder_path)
        return
    if reader is None:
        reader = easyocr.Reader(['en','ch_sim'], gpu=False)

    # build list of images and expected values
    images = []
    for f in sorted(p.iterdir()):
        if not f.is_file():
            continue
        name = f.name
        if 'QR_' not in name:
            continue
        stem = f.stem
        try:
            idx = stem.index('QR_')
            exp = stem[idx+3:idx+7]
        except Exception:
            exp = ''
        images.append((str(f), name, exp))

    # parameter grid
    param_sets = [
        {'name': 'default', 'text_threshold': 0.4, 'low_text': 0.2, 'link_threshold': 0.2},
        {'name': 'loose', 'text_threshold': 0.25, 'low_text': 0.08, 'link_threshold': 0.15},
        {'name': 'strict', 'text_threshold': 0.6, 'low_text': 0.3, 'link_threshold': 0.3},
    ]

    split_opts = ['none', 'equal']
    per_char_opts = [False, True]

    # gather variant names from a sample image
    sample_img = Image.open(images[0][0]) if images else None
    if sample_img is None:
        print('没有找到 QR 图片')
        return
    variants_sample = list(gen_variants(sample_img).keys())

    combos = list(itertools.product(variants_sample, range(len(param_sets)), split_opts, per_char_opts))
    results = []

    for variant_name, p_idx, split_opt, per_char in combos:
        total = 0
        correct = 0
        for img_path, fname, exp in images:
            total += 1
            # open and pick variant
            img = Image.open(img_path)
            vars = gen_variants(img)
            src = vars.get(variant_name, img)

            pred = None
            if per_char:
                r = per_char_recognition(reader, src, expected_len=4, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789')
                pred = r['text'] if r else None
            else:
                # whole-image recognition
                ps = param_sets[p_idx]
                r = collect_easyocr(reader, src, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789',
                                     text_threshold=ps['text_threshold'], low_text=ps['low_text'], link_threshold=ps['link_threshold'])
                pred = r['text'] if r else None
                # if split option, try equal split and prefer if higher avg prob
                if split_opt == 'equal':
                    split_src = src
                    parts = simple_vertical_split(split_src, parts=4)
                    part_texts = []
                    part_probs = []
                    for p_img in parts:
                        bestp = {'text':'', 'prob':0.0}
                        for ps2 in param_sets:
                            rr = collect_easyocr(reader, p_img, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789',
                                                 text_threshold=ps2['text_threshold'], low_text=ps2['low_text'], link_threshold=ps2['link_threshold'])
                            if rr and rr.get('text') and rr.get('prob',0) > bestp['prob']:
                                bestp = {'text': rr['text'], 'prob': rr['prob']}
                        part_texts.append(bestp['text'])
                        part_probs.append(bestp['prob'])
                    joined = ''.join([t for t in part_texts if t])
                    avgp = float(np.mean(part_probs)) if part_probs else 0.0
                    # choose joined if non-empty and avg prob >= whole-image prob
                    try:
                        wholep = r['prob'] if r else 0.0
                    except Exception:
                        wholep = 0.0
                    if joined and avgp >= wholep:
                        pred = joined

            match = False
            if pred and exp:
                if pred.strip().upper() == exp.strip().upper():
                    match = True
                    correct += 1

        acc = (correct / total * 100.0) if total else 0.0
        results.append({'variant': variant_name, 'param_idx': p_idx, 'param_name': param_sets[p_idx]['name'], 'split': split_opt, 'per_char': per_char, 'total': total, 'correct': correct, 'accuracy': acc})

    # save CSV
    out_csv = out_csv or (PROJECT_ROOT / 'ocr_model_benchmark' / 'qr_experiments.csv')
    with open(out_csv, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=['variant','param_idx','param_name','split','per_char','total','correct','accuracy'])
        writer.writeheader()
        for r in results:
            writer.writerow(r)

    # print top results
    results_sorted = sorted(results, key=lambda x: x['accuracy'], reverse=True)
    print('\nTop 10 combinations:\n')
    for r in results_sorted[:10]:
        print(r)
    print('\nCSV saved to:', out_csv)
    return results_sorted


def simple_vertical_split(img, parts=4):
    w,h = img.size
    slice_w = max(1, w//parts)
    boxes = []
    for i in range(parts):
        left = i*slice_w
        right = (i+1)*slice_w if i < parts-1 else w
        boxes.append((left,0,right,h))
    return [img.crop(b) for b in boxes]


def collect_easyocr(reader, img, allowlist=None, text_threshold=0.4, low_text=0.2, link_threshold=0.2):
    arr = np.array(img)
    try:
        res = reader.readtext(arr, detail=1, paragraph=False,
                              text_threshold=text_threshold,
                              low_text=low_text,
                              link_threshold=link_threshold,
                              allowlist=allowlist)
    except Exception as e:
        logger.warning('easyocr 识别出错: %s', e)
        return None
    texts = [t for (_, t, _) in res]
    probs = [float(p) for (_, _, p) in res] if res else []
    combined = ''.join(texts)
    avgp = float(np.mean(probs)) if probs else 0.0
    return {'text': combined, 'prob': avgp, 'raw': res}


def vertical_projection_split(img, expected_chars=4, min_region_width=4, cutoff_ratio=0.20):
    """基于垂直投影切分字符区域，返回按左到右的 PIL.Image 列表
    若无法找到合适的分割，返回等宽分割结果作为回退。
    """
    try:
        gray = img.convert('L')
        arr = np.array(gray)
        # 二值化 - 自适应OTSU-like using threshold at mean
        thresh = arr.mean()
        bw = (arr < thresh).astype(np.uint8)  # 黑字为1
        proj = bw.sum(axis=0)

        # 平滑投影以减少噪声
        kernel = np.ones(3) / 3.0
        smooth = np.convolve(proj, kernel, mode='same')

        # 寻找连续为低值的分隔区（小于最大投影的20%）
        cutoff = smooth.max() * cutoff_ratio if smooth.max() > 0 else 0
        separators = smooth <= cutoff

        # 把非分隔的连续片段视为字符区
        regions = []
        in_region = False
        start = 0
        for i, val in enumerate(separators):
            if not val and not in_region:
                in_region = True
                start = i
            elif val and in_region:
                end = i
                if end - start >= min_region_width:
                    regions.append((start, end))
                in_region = False
        if in_region:
            end = len(separators)
            if end - start >= min_region_width:
                regions.append((start, end))

        # 如果识别到的区域数量和预期不符，尝试合并或回退到等宽
        if len(regions) == expected_chars:
            boxes = [(s, 0, e, img.height) for (s, e) in regions]
            return [img.crop(b) for b in boxes]

        # 如果检测到更多区域，合并相邻最小宽度直到达到 expected_chars
        if len(regions) > expected_chars:
            # 合并最窄的间隔
            widths = [e - s for s, e in regions]
            while len(regions) > expected_chars:
                idx = int(np.argmin(widths))
                # merge idx and idx+1
                if idx < len(regions) - 1:
                    s0, e0 = regions[idx]
                    s1, e1 = regions[idx + 1]
                    regions[idx] = (s0, e1)
                    regions.pop(idx + 1)
                else:
                    # merge last two
                    s0, e0 = regions[-2]
                    s1, e1 = regions[-1]
                    regions[-2] = (s0, e1)
                    regions.pop()
                widths = [e - s for s, e in regions]
            boxes = [(s, 0, e, img.height) for (s, e) in regions]
            return [img.crop(b) for b in boxes]

        # 如果检测到较少区域，回退到等宽分割
        return simple_vertical_split(img, parts=expected_chars)
    except Exception as e:
        logger.debug('vertical_projection_split 失败: %s', e)
        return simple_vertical_split(img, parts=expected_chars)


def per_char_recognition(reader, img, expected_len=4, allowlist=None, upscale=4, char_size=48, cutoff_ratio=0.20):
    """对图像进行垂直投影分割后，对每个字符单独识别并返回拼接结果"""
    try:
        # 先放大以提高单字符识别率
        try:
            big = img.resize((img.width * upscale, img.height * upscale), Image.Resampling.LANCZOS)
        except Exception:
            big = img.resize((img.width * upscale, img.height * upscale))

        parts = vertical_projection_split(big, expected_chars=expected_len, cutoff_ratio=cutoff_ratio)
        chars = []
        probs = []
        for i, p in enumerate(parts, 1):
            # 归一化尺寸
            try:
                p2 = p.convert('L').resize((char_size, char_size), Image.Resampling.LANCZOS)
            except Exception:
                p2 = p.convert('L').resize((char_size, char_size))

            r = collect_easyocr(reader, p2, allowlist=allowlist, text_threshold=0.35, low_text=0.05, link_threshold=0.1)
            txt = r['text'] if r else ''
            prob = r['prob'] if r else 0.0
            # 如果结果是多字符，取第一个字符
            if txt and len(txt) > 1:
                txt = txt[0]
            chars.append(txt)
            probs.append(prob)
            logger.info('per-char part %d -> "%s" (p=%.3f)', i, txt, prob)

        combined = ''.join([c for c in chars if c])
        avgp = float(np.mean(probs)) if probs else 0.0
        return {'text': combined, 'prob': avgp, 'parts': chars}
    except Exception as e:
        logger.debug('per_char_recognition 失败: %s', e)
        return None


def morph_open(img, radius=1):
    try:
        # erosion then dilation approximated by MinFilter then MaxFilter
        e = img.filter(ImageFilter.MinFilter(size=3))
        for _ in range(max(0, radius-1)):
            e = e.filter(ImageFilter.MinFilter(size=3))
        d = e.filter(ImageFilter.MaxFilter(size=3))
        for _ in range(max(0, radius-1)):
            d = d.filter(ImageFilter.MaxFilter(size=3))
        return d
    except Exception:
        return img


def morph_close(img, radius=1):
    try:
        # dilation then erosion approximated by MaxFilter then MinFilter
        d = img.filter(ImageFilter.MaxFilter(size=3))
        for _ in range(max(0, radius-1)):
            d = d.filter(ImageFilter.MaxFilter(size=3))
        e = d.filter(ImageFilter.MinFilter(size=3))
        for _ in range(max(0, radius-1)):
            e = e.filter(ImageFilter.MinFilter(size=3))
        return e
    except Exception:
        return img


def pad_and_resize_char(img, target=48, pad=6):
    # convert to L, trim and center on white canvas with padding, then resize
    try:
        im = img.convert('L')
        # trim whitespace
        bg = Image.new('L', im.size, 255)
        diff = ImageChops.difference(im, bg)
        bbox = diff.getbbox()
        if bbox:
            im = im.crop(bbox)
        # create canvas
        w, h = im.size
        size = max(w, h) + pad*2
        canvas = Image.new('L', (size, size), 255)
        canvas.paste(im, ((size - w)//2, (size - h)//2))
        # resize to target
        try:
            return canvas.resize((target, target), Image.Resampling.LANCZOS)
        except Exception:
            return canvas.resize((target, target))
    except Exception:
        return img.convert('L').resize((target, target))


def normalize_prediction(txt):
    if not txt:
        return ''
    subs = {'0':'O', 'O':'O', 'o':'O', '1':'I', 'l':'I', '5':'S', 's':'S', '2':'Z', '8':'8', '6':'6', '9':'9'}
    out = ''
    for c in txt:
        if c.isalnum():
            out += subs.get(c, c).upper()
    return out


def run_targeted_qr(folder_path, reader=None, out_csv=None):
    p = Path(folder_path)
    if not p.exists() or not p.is_dir():
        print('QR 文件夹不存在：', folder_path)
        return
    if reader is None:
        reader = easyocr.Reader(['en','ch_sim'], gpu=False)

    images = []
    for f in sorted(p.iterdir()):
        if not f.is_file():
            continue
        name = f.name
        if 'QR_' not in name:
            continue
        stem = f.stem
        try:
            idx = stem.index('QR_')
            exp = stem[idx+3:idx+7]
        except Exception:
            exp = ''
        images.append((str(f), name, exp))

    # focus on median + strict params with small preprocessing choices
    params = {'text_threshold': 0.6, 'low_text': 0.3, 'link_threshold': 0.3}
    choices = [
        ('median', lambda im: im.filter(ImageFilter.MedianFilter(size=3))),
        ('median_open', lambda im: morph_open(im.filter(ImageFilter.MedianFilter(size=3)), radius=1)),
        ('median_close', lambda im: morph_close(im.filter(ImageFilter.MedianFilter(size=3)), radius=1)),
        ('median_adapt_open', lambda im: morph_open((lambda x: gen_variants(x).get('adaptive_thresh', x))(im), radius=1)),
        ('median_pad', lambda im: pad_and_resize_char(im)),
        ('8x_resize', lambda im: im.resize((im.width*8, im.height*8)))
    ]

    results = []
    for name_choice, fn in choices:
        total = 0
        correct = 0
        for img_path, fname, exp in images:
            total += 1
            img = Image.open(img_path)
            proc = fn(img)
            # whole-image
            r = collect_easyocr(reader, proc, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789',
                                 text_threshold=params['text_threshold'], low_text=params['low_text'], link_threshold=params['link_threshold'])
            whole = r['text'] if r else ''
            whole_n = normalize_prediction(whole)

            # split equal with per-part padding+resize
            parts = simple_vertical_split(proc, parts=4)
            part_chars = []
            for p_img in parts:
                p2 = pad_and_resize_char(p_img, target=48, pad=6)
                rr = collect_easyocr(reader, p2, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789',
                                      text_threshold=0.6, low_text=0.05, link_threshold=0.1)
                ch = rr['text'] if rr else ''
                chn = normalize_prediction(ch[:1]) if ch else ''
                part_chars.append(chn)
            joined = ''.join(part_chars)

            # choose between joined and whole based on length and presence
            pred = joined if len(joined) == 4 else whole_n
            if not pred:
                pred = whole_n
            match = False
            if pred and exp:
                if pred.strip().upper() == exp.strip().upper():
                    match = True
                    correct += 1
        acc = (correct/total*100.0) if total else 0.0
        results.append({'choice': name_choice, 'total': total, 'correct': correct, 'accuracy': acc})

    out_csv = out_csv or (PROJECT_ROOT / 'ocr_model_benchmark' / 'qr_targeted_results.csv')
    with open(out_csv, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=['choice','total','correct','accuracy'])
        writer.writeheader()
        for r in results:
            writer.writerow(r)

    results_sorted = sorted(results, key=lambda x: x['accuracy'], reverse=True)
    print('\nTargeted results:')
    for r in results_sorted:
        print(r)
    print('\nCSV saved to:', out_csv)
    return results_sorted


def run(image_path, expected=None, reader=None):
    img = Image.open(image_path)
    # allow passing a pre-created reader for batch runs
    if reader is None:
        reader = easyocr.Reader(['en','ch_sim'], gpu=False)
    variants = gen_variants(img)

    candidates = []
    # white-list 只允许字母数字，减少中文/符号干扰
    allow_alnum = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'

    # 尝试多组参数（默认 / 放宽阈值 / 更严格阈值）
    param_sets = [
        {'name': 'default', 'text_threshold': 0.4, 'low_text': 0.2, 'link_threshold': 0.2},
        {'name': 'loose', 'text_threshold': 0.25, 'low_text': 0.08, 'link_threshold': 0.15},
        {'name': 'strict', 'text_threshold': 0.6, 'low_text': 0.3, 'link_threshold': 0.3},
    ]

    # whole-image variants
    for name, v in variants.items():
        for p in param_sets:
            r = collect_easyocr(reader, v, allowlist=allow_alnum,
                                 text_threshold=p['text_threshold'],
                                 low_text=p['low_text'],
                                 link_threshold=p['link_threshold'])
            if r and r.get('text'):
                cand_name = f"{name}_{p['name']}"
                candidates.append({'engine': f'easy_{cand_name}', 'text': r['text'], 'prob': r['prob'], 'variant': cand_name})
                logger.info('variant %s -> %s (p=%.3f)', cand_name, r['text'], r['prob'])

    # equal-width split per variant (try the best enlarged/grayscale source)
    split_source = variants.get('4x') or variants.get('2x') or variants.get('grayscale') or img
    parts = simple_vertical_split(split_source, parts=4)
    part_texts = []
    part_probs = []
    # 对每部分使用相同的 param_sets 与白名单
    for i, p_img in enumerate(parts, 1):
        best_part = {'text': '', 'prob': 0.0, 'config': None}
        for p in param_sets:
            r = collect_easyocr(reader, p_img, allowlist=allow_alnum,
                                 text_threshold=p['text_threshold'],
                                 low_text=p['low_text'],
                                 link_threshold=p['link_threshold'])
            txt = r['text'] if r else ''
            prob = r['prob'] if r else 0.0
            logger.info('split part %d (%s) -> "%s" (p=%.3f)', i, p['name'], txt, prob)
            if txt and prob > best_part['prob']:
                best_part = {'text': txt, 'prob': prob, 'config': p['name']}
        part_texts.append(best_part['text'])
        part_probs.append(best_part['prob'])
    joined = ''.join([t for t in part_texts if t])
    avgp = float(np.mean(part_probs)) if part_probs else 0.0
    if joined:
        candidates.append({'engine': 'split_equal', 'text': joined, 'prob': avgp, 'variant': 'equal_split'})
        logger.info('split joined -> %s (avgp=%.3f)', joined, avgp)

    # 逐字符识别已移除；仅使用整图与等宽分割候选

    # choose best: prefer len==4 then highest prob
    candidates = [c for c in candidates if c.get('text')]
    logger.info('candidates: %s', candidates)
    if not candidates:
        logger.warning('无候选结果')
        return None
    len4 = [c for c in candidates if len(c['text'])==4]
    if len4:
        best = max(len4, key=lambda x: x['prob'])
    else:
        best = max(candidates, key=lambda x: x['prob'])
    logger.info('选择最终: %s (engine=%s, prob=%.3f)', best['text'], best['engine'], best['prob'])
    return best['text']


if __name__ == '__main__':
    # 支持三种用法：
    # 1) 指定单张图片: python easyocr_simplified.py path/to/img.png
    # 2) 指定单张图片并传入期望值: python easyocr_simplified.py path/to/img.png expected
    # 3) 不传参或传入 'QR' -> 批量评估项目下 ocr_model_benchmark/QR 文件夹内以 QR_ 开头的图片
    if len(sys.argv) < 2 or sys.argv[1].lower() == 'qr':
        # 批量评估 QR 文件夹
        qr_folder = PROJECT_ROOT / 'ocr_model_benchmark' / 'QR'
        def evaluate_folder(folder_path):
            p = Path(folder_path)
            if not p.exists() or not p.is_dir():
                print('QR 文件夹不存在：', folder_path)
                return
            reader = easyocr.Reader(['en','ch_sim'], gpu=False)
            # 简化输出：在批量评估期间抑制详细 info 日志
            old_level = logger.level
            logger.setLevel(logging.WARNING)
            total = 0
            correct = 0
            details = []
            for f in sorted(p.iterdir()):
                if not f.is_file():
                    continue
                name = f.name
                if 'QR_' not in name:
                    continue
                # 解析 QR_ 后的 4 个字符为期望值
                stem = f.stem
                try:
                    idx = stem.index('QR_')
                    exp = stem[idx+3:idx+7]
                except Exception:
                    exp = ''
                total += 1
                pred = run(str(f), expected=exp, reader=reader)
                match = False
                if pred and exp:
                    if pred.strip().upper() == exp.strip().upper():
                        match = True
                        correct += 1
                details.append((name, exp, pred, match))
                print(f'{name} expected={exp} pred={pred} match={match}')
            acc = (correct/total*100) if total else 0.0
            # 恢复日志级别
            logger.setLevel(old_level)
            print('\nSUMMARY: total=%d correct=%d accuracy=%.2f%%' % (total, correct, acc))
            return {'total': total, 'correct': correct, 'accuracy': acc, 'details': details}

        evaluate_folder(qr_folder)
    elif sys.argv[1].lower() == 'qr_exp':
        qr_folder = PROJECT_ROOT / 'ocr_model_benchmark' / 'QR'
        # 运行实验网格并生成 CSV
        reader = easyocr.Reader(['en','ch_sim'], gpu=False)
        run_experiments_qr(qr_folder, reader=reader)
    elif sys.argv[1].lower() == 'qr_target':
        qr_folder = PROJECT_ROOT / 'ocr_model_benchmark' / 'QR'
        reader = easyocr.Reader(['en','ch_sim'], gpu=False)
        run_targeted_qr(qr_folder, reader=reader)
    else:
        image_path = sys.argv[1]
        expected = sys.argv[2] if len(sys.argv)>2 else None
        t0 = time.time()
        res = run(image_path, expected)
        t1 = time.time()
        print('RESULT:', res)
        print('TIME: %.3fs' % (t1-t0))
