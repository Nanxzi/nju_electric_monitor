# 简单的运行包装器，启用 faulthandler 并捕获异常，写入 logs 目录供 CI 排查
import faulthandler
import sys
import os
import traceback
from datetime import datetime
import json

# 启用 faulthandler 全局输出（默认输出到 stderr）
# faulthandler.register 在某些 Python 版本不可用，使用 enable(file=...) 替代

# 确保 logs 目录存在
log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
os.makedirs(log_dir, exist_ok=True)
log_path = os.path.join(log_dir, f'workflow_wrapper_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')

try:
    # 将 stdout/stderr 同步到日志文件，避免丢失信息
    with open(log_path, 'w', encoding='utf-8', errors='replace') as lf:
        lf.write(f'Wrapper start: {datetime.now().isoformat()}\n')
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
            # 只打印前 200 个 key 名以避免日志过大
            for k in keys[:200]:
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

        # 将 Python 的 stdout/stderr 重定向到日志文件，捕获第三方库输出
        import contextlib
        with contextlib.redirect_stdout(lf), contextlib.redirect_stderr(lf):
            # 延迟导入主模块，便于捕获导入阶段的错误
            try:
                from nju_electric_monitor_workflow import main
            except Exception as e:
                lf.write('Import main failed:\n')
                traceback.print_exc(file=lf)
                lf.flush()
                raise

            try:
                # 运行主函数并捕获异常
                main()
            except SystemExit as se:
                lf.write(f'SystemExit: {se}\n')
                raise
            except Exception:
                lf.write('Unhandled exception in main:\n')
                traceback.print_exc(file=lf)
                lf.flush()
                # 触发 faulthandler 写入附加信息
                try:
                    faulthandler.dump_traceback(file=lf)
                except Exception:
                    lf.write('faulthandler.dump_traceback failed\n')
                raise
            finally:
                lf.write(f'Wrapper end: {datetime.now().isoformat()}\n')
                lf.flush()

except Exception:
    # 保持异常向上，让 CI 能够看到非零退出码
    raise
