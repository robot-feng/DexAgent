# Feishu CLI for project maintenance

The Feishu CLI is an optional maintainer tool for syncing project documentation and progress to the Feishu knowledge base. It is not a runtime dependency of DexAgent or EmbodiForge Studio.

Follow the [official Feishu CLI installation guide](https://open.feishu.cn/document/no_class/mcp-archive/feishu-cli-installation-guide.md). The npm installation path requires Node.js and npm/npx; Go 1.23+ and Python 3 are needed only when building from source.

```bash
npm install -g @larksuite/cli
npx -y skills add https://open.feishu.cn --skill -y
```

Initialize application credentials only on a machine that has not been configured before:

```bash
lark-cli config init --new
```

Keep app credentials and tokens in the CLI's local credential store. Never commit them to this repository. Then sign in through the browser/device flow and verify the identity:

```bash
lark-cli auth login --recommend
lark-cli auth status --json --verify
```

Wiki knowledge spaces and nodes are user resources. Use `--as user` for Wiki reads and writes; bot credentials do not substitute for the user's identity. Complete the browser authorization before attempting to sync a personal knowledge base.
