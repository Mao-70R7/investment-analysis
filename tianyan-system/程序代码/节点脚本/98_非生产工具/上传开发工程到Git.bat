@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "REPO_ROOT=%~dp0"
cd /d "%REPO_ROOT%"
title 上传开发工程到 Git

echo ============================================================================
echo 开发工程 Git 上传工具
echo 仓库目录：%REPO_ROOT%
echo ============================================================================
echo.

if not exist "%REPO_ROOT%.git" (
    echo [失败] 当前目录不是开发工程 Git 仓库：%REPO_ROOT%
    exit /b 2
)

set "BRANCH="
for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "BRANCH=%%B"
if not defined BRANCH (
    echo [失败] 无法识别当前分支，或当前处于 detached HEAD 状态。
    exit /b 3
)

set "REMOTE_URL="
for /f "delims=" %%R in ('git remote get-url origin 2^>nul') do set "REMOTE_URL=%%R"
if not defined REMOTE_URL (
    echo [失败] 未配置 origin 远程仓库。
    exit /b 4
)

set "AHEAD=0"
set "BEHIND=0"
for /f "tokens=1,2" %%A in ('git rev-list --left-right --count "origin/!BRANCH!...!BRANCH!" 2^>nul') do (
    set "BEHIND=%%A"
    set "AHEAD=%%B"
)

echo 当前分支：!BRANCH!
echo 远程仓库：!REMOTE_URL!
echo 与 origin/!BRANCH! 的差异：本地领先 !AHEAD! 个提交，本地落后 !BEHIND! 个提交
echo.
echo [当前状态]
git status --short --branch
if errorlevel 128 (
    echo [失败] 无法读取 Git 状态。
    exit /b 5
)
echo.
echo [未提交变更统计]
git diff --stat
git diff --cached --stat
echo.

set "HAS_CHANGES="
for /f "delims=" %%S in ('git status --porcelain 2^>nul') do set "HAS_CHANGES=1"
if not defined HAS_CHANGES (
    if not "!AHEAD!"=="0" (
        echo 当前没有未提交变更，但本地存在尚未推送的提交。
        set "PUSH_EXISTING_CONFIRM="
        set /p "PUSH_EXISTING_CONFIRM=是否直接推送已有本地提交？(Y/N)："
        if /I "!PUSH_EXISTING_CONFIRM!"=="Y" (
            git push origin "!BRANCH!"
            if errorlevel 1 (
                echo [失败] 已有本地提交推送失败，请检查远程是否有新提交或网络权限。
                exit /b 11
            )
            echo [完成] 已有本地提交已推送到 origin/!BRANCH!。
            exit /b 0
        )
    )
    echo [完成] 当前开发工程没有未提交变更，无需上传。
    exit /b 0
)

if /I "%~1"=="/ReviewOnly" goto :review_only
if /I "%~1"=="-ReviewOnly" goto :review_only

echo 注意：下一步会把所有未被 .gitignore 忽略的变更加入暂存区。
echo 请先根据上面的状态和统计确认是否包含临时文件、测试文件或不应上传的数据。
echo.
set "STAGE_CONFIRM="
set /p "STAGE_CONFIRM=是否继续暂存全部变更？(Y/N)："
if /I not "!STAGE_CONFIRM!"=="Y" goto :cancelled

echo.
echo [1/3] 暂存全部变更...
git add -A
if errorlevel 1 (
    echo [失败] git add -A 执行失败，未提交任何内容。
    exit /b 6
)

echo.
echo [待提交文件]
git diff --cached --name-status
echo.
git diff --cached --check
if errorlevel 1 (
    echo [失败] 暂存内容存在空白字符错误，请修复后重新运行。
    exit /b 7
)

git diff --cached --quiet
if not errorlevel 1 (
    echo [完成] 暂存后没有可提交变更。
    exit /b 0
)

set "COMMIT_MESSAGE="
set /p "COMMIT_MESSAGE=请输入提交说明（直接回车使用默认说明）："
if not defined COMMIT_MESSAGE set "COMMIT_MESSAGE=更新开发工程代码 %date% %time%"

echo.
echo [2/3] 提交开发工程...
git commit -m "!COMMIT_MESSAGE!"
if errorlevel 1 (
    echo [失败] git commit 执行失败。暂存内容仍保留，可修复后重新运行。
    exit /b 8
)

echo.
set "PUSH_CONFIRM="
set /p "PUSH_CONFIRM=是否推送到 origin/!BRANCH!？(Y/N)："
if /I not "!PUSH_CONFIRM!"=="Y" goto :committed_not_pushed

echo.
echo [3/3] 推送开发工程...
git push origin "!BRANCH!"
if errorlevel 1 (
    echo [失败] git push 执行失败。本地提交已完成，可检查网络或权限后重新运行。
    exit /b 9
)

echo.
echo [远程校验]
git ls-remote origin "refs/heads/!BRANCH!"
if errorlevel 1 (
    echo [失败] 无法校验远程分支。
    exit /b 10
)
echo.
echo [完成] 开发工程已提交并推送到 origin/!BRANCH!。
exit /b 0

:review_only
echo.
echo [只查看模式] 未执行暂存、提交或推送。
exit /b 0

:committed_not_pushed
echo.
echo [已提交] 本地提交已完成，但未推送到远程仓库。
echo 后续可重新运行本脚本，并在暂存确认前检查当前状态。
exit /b 0

:cancelled
echo.
echo [已取消] 未暂存、未提交、未推送任何内容。
exit /b 0
