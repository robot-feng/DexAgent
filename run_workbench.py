"""Start the optional local visualization service without starting a pipeline."""
from __future__ import annotations

import argparse
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--runs-root', type=Path)
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--artifact-root', type=Path, action='append', default=[])
    parser.add_argument('--scene-root', type=Path, action='append', default=[])
    parser.add_argument('--editor-python', type=Path)
    parser.add_argument('--robot-assets', type=Path)
    parser.add_argument('--frontend-dist', type=Path, help='serve a separate frontend build for isolated previews')
    parser.add_argument('--openapi', type=Path, help='write the API schema and exit')
    args = parser.parse_args(argv)
    try:
        from workbench.api import create_app
        import uvicorn
    except ImportError as exc:
        parser.exit(2, f'Workbench dependency missing: {exc}. Install requirements-workbench.txt.\n')
    app = create_app(runs_root=args.runs_root, state_dir=args.state_dir,
                     artifact_roots=tuple(args.artifact_root), scene_roots=tuple(args.scene_root),
                     editor_python=args.editor_python, robot_assets=args.robot_assets, frontend_dist=args.frontend_dist)
    if args.openapi:
        import json
        args.openapi.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + '\n')
        return 0
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
