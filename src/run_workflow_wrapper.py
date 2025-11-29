# 简单的运行包装器，启用 faulthandler 并捕获异常，写入 logs 目录供 CI 排查
import faulthandler
import sys
import os
import traceback
from datetime import datetime

faulthandler.enable()

# 确保 logs 目录存在
log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
os.makedirs(log_dir, exist_ok=True)
log_path = os.path.join(log_dir, f'workflow_wrapper_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')

try:
    # 将 stdout/stderr 同步到日志文件，避免丢失信息
    with open(log_path, 'w', encoding='utf-8', errors='replace') as lf:
        lf.write(f'Wrapper start: {datetime.now().isoformat()}\n')
        lf.flush()
        # 将 faulthandler 输出到文件也能帮助定位 native crash
        faulthandler.register(lf)
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
            faulthandler.dump_traceback(file=lf)
            raise
        finally:
            lf.write(f'Wrapper end: {datetime.now().isoformat()}\n')
            lf.flush()

except Exception:
    # 保持异常向上，让 CI 能够看到非零退出码
    raise
