# 终端现代化改造：一键安装脚本

## 概述
本指南提供一键脚本，自动安装缺失的现代终端工具，并配置 shell 别名和集成。基于用户选择的替换方案：

| 传统命令 | 现代替代 | 已安装状态 |
|----------|----------|------------|
| `find`   | `fd`     | ✅ 已安装 |
| `grep`   | `ripgrep` (`rg`) | ✅ 已安装 |
| `ls`     | `eza`    | ✅ 已安装 |
| `cat`    | `bat`    | ✅ 已安装 |
| `top`    | `btop`   | ❌ 未安装 |
| `du`     | `dust`   | ❌ 未安装 |
| `cd`     | `zoxide` | ✅ 已安装 |
| `fzf`    | `fzf`    | ❌ 未安装（可选模糊搜索） |

## 一键安装脚本
将以下脚本保存为 `modernize-terminal.sh` 并执行：

```bash
#!/usr/bin/env bash
set -euo pipefail

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 辅助函数
info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# 检查依赖
command -v cargo >/dev/null 2>&1 || error "需要安装 Rust/Cargo: https://rustup.rs"
command -v apt >/dev/null 2>&1 || error "需要 apt 包管理器（Ubuntu/Debian）"

# 更新包列表
info "更新包列表..."
sudo apt update -qq

# 安装缺失工具
install_apt() {
    local pkg=$1
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
        info "安装 $pkg..."
        sudo apt install -y "$pkg"
    else
        info "$pkg 已安装"
    fi
}

install_cargo() {
    local bin=$1
    local pkg=${2:-$1}
    if ! command -v "$bin" >/dev/null 2>&1; then
        info "通过 Cargo 安装 $pkg..."
        cargo install "$pkg"
    else
        info "$bin 已安装"
    fi
}

# 安装 btop（系统监控）
install_apt btop

# 安装 dust（磁盘使用）
install_cargo dust du-dust

# 安装 fzf（模糊搜索）
if ! command -v fzf >/dev/null 2>&1; then
    info "安装 fzf..."
    git clone --depth 1 https://github.com/junegunn/fzf.git ~/.fzf
    ~/.fzf/install --all --no-bash --no-zsh --no-fish
fi

# 备份现有 .bashrc
BASHRC="$HOME/.bashrc"
BACKUP="$HOME/.bashrc.backup.$(date +%Y%m%d_%H%M%S)"
if [[ -f "$BASHRC" ]]; then
    cp "$BASHRC" "$BACKUP"
    info "已备份 .bashrc 到 $BACKUP"
fi

# 创建或更新 .bash_aliases
ALIASES="$HOME/.bash_aliases"
cat > "$ALIASES" << 'EOF'
# 现代终端工具别名
# 文件操作
alias ls='eza --icons --group-directories-first'
alias ll='eza -la --icons --group-directories-first'
alias la='eza -a --icons'
alias l.='eza -d .*'
alias find='fd'

# 内容搜索
alias grep='rg'
alias egrep='rg --pcre2'
alias fgrep='rg --fixed-strings'

# 文件查看
alias cat='bat --paging=never --style=auto'
alias catp='bat --paging=always --style=full'

# 磁盘使用
alias du='dust'
alias dust='dust --reverse'

# 系统监控
alias top='btop'
alias htop='btop'

# 目录导航
alias cd='z'

# 模糊搜索（需安装 fzf）
alias ff='fd --type f --hidden --follow --exclude .git | fzf --preview "bat --color=always --style=numbers --line-range=:500 {}"'
alias fg='rg --hidden --no-heading --line-number --color=always . --glob "!.git" | fzf --delimiter ":" --preview "bat --color=always --style=numbers --line-range=:500 {1}"'

# Git 别名（可选）
alias gs='git status'
alias ga='git add'
alias gc='git commit'
alias gp='git push'
alias gl='git log --oneline --graph --decorate'

# 安全确认
alias rm='rm -i'
alias mv='mv -i'
alias cp='cp -i'
EOF

# 在 .bashrc 中添加 zoxide 初始化（如果尚未添加）
if ! grep -q "zoxide init bash" "$BASHRC" 2>/dev/null; then
    echo '' >> "$BASHRC"
    echo '# zoxide 初始化' >> "$BASHRC"
    echo 'eval "$(zoxide init bash)"' >> "$BASHRC"
    info "已添加 zoxide 初始化到 .bashrc"
fi

# 在 .bashrc 中添加 fzf 初始化（如果尚未添加）
if [[ -f ~/.fzf.bash ]] && ! grep -q "source ~/.fzf.bash" "$BASHRC" 2>/dev/null; then
    echo '' >> "$BASHRC"
    echo '# fzf 初始化' >> "$BASHRC"
    echo 'source ~/.fzf.bash' >> "$BASHRC"
    info "已添加 fzf 初始化到 .bashrc"
fi

# 重新加载 .bashrc
info "重新加载 .bashrc..."
source "$BASHRC" 2>/dev/null || warn "请手动运行 'source ~/.bashrc' 或重新打开终端"

# 验证安装
info "验证安装..."
echo "fd: $(command -v fd)"
echo "rg: $(command -v rg)"
echo "bat: $(command -v bat)"
echo "eza: $(command -v eza)"
echo "btop: $(command -v btop)"
echo "dust: $(command -v dust)"
echo "zoxide: $(command -v zoxide)"
echo "fzf: $(command -v fzf)"

info "安装完成！请重新打开终端或运行 'source ~/.bashrc' 使别名生效。"
info "使用 'ls' 测试 eza，'cat' 测试 bat，'cd' 测试 zoxide。"
info "回滚方法：恢复备份文件 '$BACKUP' 并删除 '$ALIASES'。"
```

## 手动安装（如脚本失败）

### 1. 安装缺失工具
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install -y btop

# 通过 Cargo 安装 dust
cargo install du-dust

# 安装 fzf
git clone --depth 1 https://github.com/junegunn/fzf.git ~/.fzf
~/.fzf/install --all
```

### 2. 配置别名
创建或编辑 `~/.bash_aliases`，添加上述别名内容。

### 3. 更新 .bashrc
确保 `.bashrc` 中包含以下行：
```bash
# zoxide 初始化
eval "$(zoxide init bash)"

# fzf 初始化（如果安装了）
source ~/.fzf.bash
```

### 4. 重新加载
```bash
source ~/.bashrc
```

## 验证测试
```bash
# 测试 eza
ls -la

# 测试 bat
cat /etc/hostname

# 测试 fd
find / -name "*.conf" 2>/dev/null | head -5

# 测试 ripgrep
grep -r "function" ~/.bashrc

# 测试 zoxide
cd /tmp
cd -

# 测试 dust
du -sh ~

# 测试 btop
top
```

## 回滚方法
1. 恢备份份的 `.bashrc`：
   ```bash
   cp ~/.bashrc.backup.* ~/.bashrc
   ```
2. 删除别名文件：
   ```bash
   rm ~/.bash_aliases
   ```
3. 重新加载：
   ```bash
   source ~/.bashrc
   ```

## 注意事项
- 别名会覆盖传统命令的行为（如 `ls` 现在调用 `eza`）。
- 如果遇到问题，可临时禁用别名：`unalias ls`。
- `fzf` 安装后需重新启动终端或手动 source。
- 所有工具均保持向后兼容，传统命令选项仍然有效（通过各自工具的兼容模式）。
- 本脚本假设 Ubuntu/Debian 系统，其他发行版请调整包管理器命令。

## 自定义
- 编辑 `~/.bash_aliases` 添加个人别名。
- 调整 `eza` 参数：`--icons`、`--group-directories-first` 等。
- 修改 `bat` 主题：`export BAT_THEME="Dracula"`。
- 配置 `fzf` 预览：设置 `FZF_DEFAULT_OPTS` 环境变量。

完成以上步骤后，您的终端将焕然一新，享受更快、更友好的命令行体验！