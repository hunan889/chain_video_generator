# Claude Code 二进制文件

此目录包含 Claude Code 的预编译二进制文件，用于一键启动。

## 文件信息

- **claude** - Claude Code v2.1.63 (Linux x86_64)
- **大小**: 225MB
- **架构**: ELF 64-bit LSB executable
- **系统**: GNU/Linux 3.2.0+

## 使用方法

直接运行项目根目录的启动脚本：

```bash
bash launch_claude.sh
```

启动脚本会自动检测并使用此二进制文件。

## 注意事项

1. 此二进制文件仅适用于 Linux x86_64 系统
2. 如果您使用其他操作系统（macOS、Windows），请手动安装 Claude Code
3. 二进制文件较大（225MB），Git LFS 会自动处理

## 手动安装 Claude Code

如果打包的二进制文件不适用于您的系统，请访问：

- Linux/macOS: `curl -fsSL https://claude.ai/install.sh | sh`
- 官方文档: https://docs.anthropic.com/claude/docs/claude-code
