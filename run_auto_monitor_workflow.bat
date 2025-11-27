@echo off

chcp 65001 >nul
echo ================================================
echo 【南京大学电费监控脚本（GitHub Workflow 模式）】
echo ================================================
echo.

echo 【正在检查环境...】
python tests\test_environment.py

echo.
echo 【正在运行主脚本...】
python src\nju_electric_monitor_auto.py

echo.
echo 【检测文件变更并自动提交到GitHub...】
git status
for /f "tokens=1-2 delims=." %%a in ('wmic os get localdatetime ^| findstr /r /c:"^[0-9]"') do set dt=%%a
set commitmsg=%dt:~0,4%-%dt:~4,2%-%dt:~6,2% %dt:~8,2%:%dt:~10,2% 自动提交电费变更

git add . ":!config.json" 
git commit -m "%commitmsg%"
git pull --rebase
git push

echo.
echo 【脚本运行完成】
