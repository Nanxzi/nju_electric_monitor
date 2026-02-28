# 简单的运行包装器，启用 faulthandler 并捕获异常，写入 logs 目录供 CI 排查
import faulthandler
import sys
import os
import traceback
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
    BEIJING_TZ = ZoneInfo('Asia/Shanghai')
except Exception:
    BEIJING_TZ = None
import json

# 启用 faulthandler 全局输出（默认输出到 stderr）
# faulthandler.register 在某些 Python 版本不可用，使用 enable(file=...) 替代

# 确保 logs 目录存在
log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
os.makedirs(log_dir, exist_ok=True)
log_time = datetime.now(BEIJING_TZ) if BEIJING_TZ else datetime.now()
log_path = os.path.join(log_dir, f'workflow_wrapper_{log_time.strftime("%Y%m%d_%H%M%S")}.log')

try:
    # 将 stdout/stderr 同步到日志文件，避免丢失信息
    with open(log_path, 'w', encoding='utf-8', errors='replace') as lf:
        start_time = datetime.now(BEIJING_TZ) if BEIJING_TZ else datetime.now()
        lf.write(f'Wrapper start: {start_time.isoformat()}\n')
        lf.flush()
        # 使用 faulthandler 输出到文件以便捕获 native crash
        try:
            faulthandler.enable(file=lf)
        except Exception:
            # 如果 enable 出现问题，仍继续运行并记录警告
            lf.write('faulthandler.enable failed\n')
            lf.flush()

        # 记录运行环境概要（只列出环境变量名，避免打印 secrets 值）
        try:
            lf.write('\n=== ENVIRONMENT SUMMARY ===\n')
            keys = sorted(list(os.environ.keys()))
            lf.write('Environment variable keys (count=%d):\n' % len(keys))
            # 只打印前 10 个 key 名以避免日志过大
            for k in keys[:10]:
                lf.write(f"- {k}\n")
            lf.write(f"NJU_USERNAME set: {'yes' if os.environ.get('NJU_USERNAME') else 'no'}\n")
            lf.write(f"NJU_PASSWORD set: {'yes' if os.environ.get('NJU_PASSWORD') else 'no'}\n")
            lf.flush()
        except Exception:
            lf.write('Failed to write environment summary\n')
            lf.flush()

        # 打印磁盘使用情况（df -h）和工作目录关键子目录大小
        try:
            import subprocess
            lf.write('\n=== DISK USAGE (df -h) ===\n')
            try:
                out = subprocess.check_output(['df', '-h']).decode('utf-8', errors='replace')
                lf.write(out + '\n')
            except Exception as e:
                lf.write(f'df -h failed: {e}\n')
            lf.write('\n=== KEY DIR SIZES ===\n')
            for p in ['logs', 'data', 'models']:
                path = os.path.join(os.path.dirname(__file__), '..', p)
                if os.path.exists(path):
                    try:
                        out = subprocess.check_output(['du', '-sh', path]).decode('utf-8', errors='replace')
                        lf.write(out + '\n')
                    except Exception as e:
                        lf.write(f'du -sh {path} failed: {e}\n')
                else:
                    lf.write(f'{p}: not found\n')
            lf.flush()
        except Exception:
            lf.write('Failed to record disk usage\n')
            lf.flush()

        # 打印 config_workflow.json 的非敏感摘要（掩码 username/password）
        try:
            cfg_path = os.path.join(os.path.dirname(__file__), '..', 'config_workflow.json')
            lf.write('\n=== CONFIG_WORKFLOW SUMMARY ===\n')
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, 'r', encoding='utf-8') as cf:
                        cfg = json.load(cf)
                    masked = dict(cfg)
                    if 'username' in masked:
                        masked['username'] = '***' if masked['username'] else ''
                    if 'password' in masked:
                        masked['password'] = '***' if masked['password'] else ''
                    lf.write(json.dumps(masked, ensure_ascii=False, indent=2) + '\n')
                except Exception as e:
                    lf.write(f'Failed to read/parse config_workflow.json: {e}\n')
            else:
                lf.write('config_workflow.json not found\n')
            lf.flush()
        except Exception:
            lf.write('Failed to record config summary\n')
            lf.flush()

        # 字体诊断：尝试列出 matplotlib FontManager 条目、系统字体文件，并测试首选字体的 findfont 结果
        try:
            lf.write('\n=== FONT DIAGNOSTICS ===\n')
            import importlib
            fm = None
            try:
                fm = importlib.import_module('matplotlib.font_manager')
            except Exception as e:
                lf.write(f'matplotlib.font_manager not available: {e}\n')
                fm = None

            if fm is not None:
                try:
                    # findSystemFonts 可能返回大量结果，限制输出数量
                    try:
                        system_fonts = fm.findSystemFonts(fontpaths=None, fontext='ttf')
                        lf.write(f'Found system fonts (ttf) count: {len(system_fonts)}\n')
                        for fpath in system_fonts[:50]:
                            lf.write(f'- {fpath}\n')
                    except Exception as e:
                        lf.write(f'fm.findSystemFonts failed: {e}\n')

                    try:
                        lf.write('\nFontManager samples (name -> path):\n')
                        ttflist = getattr(fm.fontManager, 'ttflist', [])
                        for entry in ttflist[:50]:
                            try:
                                name = getattr(entry, 'name', None) or getattr(entry, 'fname', None)
                                fname = getattr(entry, 'fname', None)
                                lf.write(f"{name} -> {fname}\n")
                            except Exception:
                                continue
                    except Exception as e:
                        lf.write(f'FontManager listing failed: {e}\n')

                    preferred = ['Microsoft YaHei', 'Segoe UI', 'Arial Unicode MS', 'Noto Sans CJK SC', 'WenQuanYi Micro Hei', 'WenQuanYi Zen Hei', 'DejaVu Sans']
                    lf.write('\nPreferred font find results:\n')
                    for p in preferred:
                        try:
                            fp = fm.findfont(p, fallback_to_default=False)
                            lf.write(f"findfont('{p}') -> {fp}\n")
                        except Exception as e:
                            lf.write(f"findfont('{p}') failed: {e}\n")
                    lf.flush()
                except Exception as e:
                    lf.write(f'Error during matplotlib font diagnostics: {e}\n')
                    lf.flush()
            else:
                lf.write('Skipping matplotlib font diagnostics because matplotlib.font_manager is unavailable\n')
                lf.flush()

            # 在 Linux 环境下尝试运行 fc-list 获取 fontconfig 列表（只抓取部分输出防止日志过大）
            try:
                import subprocess
                out = subprocess.check_output(['fc-list', '--format', '%{family} - %{file}\n'], stderr=subprocess.STDOUT).decode('utf-8', errors='replace')
                lf.write('\nfc-list output sample (truncated to 10000 chars):\n')
                lf.write(out[:10000] + '\n')
            except Exception as e:
                lf.write(f'fc-list failed or not available: {e}\n')
            lf.flush()
        except Exception:
            lf.write('Font diagnostics failed\n')
            lf.flush()

        # 通过子进程直接运行 nju_electric_monitor_workflow.py，避免导入路径问题
        try:
            import subprocess
            workflow_path = os.path.join(os.path.dirname(__file__), 'nju_electric_monitor_workflow.py')
            lf.write(f"Running workflow script: {workflow_path}\n")
            lf.flush()

            result = subprocess.run(
                [sys.executable, workflow_path],
                stdout=lf,
                stderr=lf,
            )

            if result.returncode != 0:
                lf.write(f"Workflow script exited with code {result.returncode}\n")
                lf.flush()
                raise SystemExit(result.returncode)

        except Exception:
            # 记录任何在运行 workflow 脚本时发生的异常
            lf.write('Error while running workflow script via subprocess:\n')
            traceback.print_exc(file=lf)
            lf.flush()
            try:
                faulthandler.dump_traceback(file=lf)
            except Exception:
                lf.write('faulthandler.dump_traceback failed\n')
            raise
        finally:
            end_time = datetime.now(BEIJING_TZ) if BEIJING_TZ else datetime.now()
            lf.write(f'Wrapper end: {end_time.isoformat()}\n')
            lf.flush()

except Exception:
    # 保持异常向上，让 CI 能够看到非零退出码
    raise
