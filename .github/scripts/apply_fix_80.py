from pathlib import Path

workspace = Path('tools/workspace.py')
text = workspace.read_text(encoding='utf-8')

old = '''def enter(paths: WorkspacePaths) -> int:\n    prepare(paths)\n    env = build_environment(paths)\n'''
new = '''def require_runtime_ready(paths: WorkspacePaths) -> None:\n    if paths.foundation_python.is_file():\n        return\n    raise WorkspaceError(\n        "Foundation runtime is not ready; managed Python was not found at "\n        f"{paths.foundation_python}. Build/configure a Recipe first, for example: "\n        "python3.12 tools/recipe.py configure --recipe <recipe.yaml>"\n    )\n\n\ndef enter(paths: WorkspacePaths) -> int:\n    require_runtime_ready(paths)\n    prepare(paths)\n    env = build_environment(paths)\n'''
assert text.count(old) == 1, text.count(old)
text = text.replace(old, new)

old = '''def run_command(paths: WorkspacePaths, command: Sequence[str]) -> int:\n    values = list(command)\n'''
new = '''def run_command(paths: WorkspacePaths, command: Sequence[str]) -> int:\n    require_runtime_ready(paths)\n    values = list(command)\n'''
assert text.count(old) == 1, text.count(old)
workspace.write_text(text.replace(old, new), encoding='utf-8')

test = Path('tools/test_workspace.py')
text = test.read_text(encoding='utf-8')
old = '''    def test_run_command_does_not_inherit_pythonpath(self) -> None:\n        script = "import os, sys; sys.exit(0 if 'PYTHONPATH' not in os.environ else 9)"\n        with mock.patch.dict(os.environ, {"PYTHONPATH": "/legacy"}, clear=False):\n            result = workspace.run_command(\n                self.paths,\n                [sys.executable, "-c", script],\n            )\n        self.assertEqual(result, 0)\n'''
new = '''    def test_enter_fails_closed_when_foundation_runtime_is_missing(self) -> None:\n        with mock.patch.object(workspace, "prepare") as prepare, mock.patch.object(\n            workspace.subprocess, "run"\n        ) as run:\n            with self.assertRaisesRegex(\n                workspace.WorkspaceError,\n                "Foundation runtime is not ready",\n            ) as raised:\n                workspace.enter(self.paths)\n        self.assertIn(str(self.paths.foundation_python), str(raised.exception))\n        self.assertIn("tools/recipe.py configure", str(raised.exception))\n        prepare.assert_not_called()\n        run.assert_not_called()\n\n    def test_run_command_fails_closed_when_foundation_runtime_is_missing(self) -> None:\n        with mock.patch.object(workspace, "prepare") as prepare, mock.patch.object(\n            workspace.subprocess, "run"\n        ) as run:\n            with self.assertRaisesRegex(\n                workspace.WorkspaceError,\n                "Foundation runtime is not ready",\n            ) as raised:\n                workspace.run_command(self.paths, ["python", "-V"])\n        self.assertIn(str(self.paths.foundation_python), str(raised.exception))\n        self.assertIn("tools/recipe.py configure", str(raised.exception))\n        prepare.assert_not_called()\n        run.assert_not_called()\n\n    def test_run_command_does_not_inherit_pythonpath(self) -> None:\n        self.paths.foundation_python.parent.mkdir(parents=True, exist_ok=True)\n        self.paths.foundation_python.write_text("", encoding="utf-8")\n        script = "import os, sys; sys.exit(0 if 'PYTHONPATH' not in os.environ else 9)"\n        with mock.patch.dict(os.environ, {"PYTHONPATH": "/legacy"}, clear=False):\n            result = workspace.run_command(\n                self.paths,\n                [sys.executable, "-c", script],\n            )\n        self.assertEqual(result, 0)\n'''
assert text.count(old) == 1, text.count(old)
test.write_text(text.replace(old, new), encoding='utf-8')

guide = Path('docs/getting-started-ja.md')
text = guide.read_text(encoding='utf-8')
old = '''~~~bash\n# 1. 隔離された作業シェルに入る（プロンプト先頭に (hako) が付く）\npython3.12 tools/workspace.py enter      # Windows: py -3.12 tools\\workspace.py enter\n\n# 2. 何が足りないか確認（ビルドはしない）\npython3.12 tools/recipe.py doctor --recipe recipes/examples/mujoco-turtlebot3-wall-follower.yaml\n\n# 3. これから何を clone / build するか確認\npython3.12 tools/recipe.py plan   --recipe recipes/examples/mujoco-turtlebot3-wall-follower.yaml\n\n# 4. Foundation を構築（clone -> build -> install -> Python 依存導入）  ※初回は 10 分前後\npython3.12 tools/recipe.py configure --recipe recipes/examples/mujoco-turtlebot3-wall-follower.yaml\n\n# 5. 使い方ガイド（HTML）を生成してブラウザで開く（configure 完了後は `python` で OK）\npython tools/recipe.py guide  --recipe recipes/examples/mujoco-turtlebot3-wall-follower.yaml --open\n~~~\n\n`configure` が完了すると、`(hako)` シェルの `python` は Foundation venv の Python 3.12 を指すようになります。以後の操作は `python` で構いません。\n\n手順 4 の最後に次のように出れば Foundation は完成です。\n'''
new = '''~~~bash\n# 1. 何が足りないか確認（ビルドはしない）\npython3.12 tools/recipe.py doctor --recipe recipes/examples/mujoco-turtlebot3-wall-follower.yaml\n\n# 2. これから何を clone / build するか確認\npython3.12 tools/recipe.py plan   --recipe recipes/examples/mujoco-turtlebot3-wall-follower.yaml\n\n# 3. Foundation を構築（clone -> build -> install -> Python 依存導入）  ※初回は 10 分前後\npython3.12 tools/recipe.py configure --recipe recipes/examples/mujoco-turtlebot3-wall-follower.yaml\n\n# 4. Foundation runtime が隔離環境として利用可能か確認\npython3.12 tools/workspace.py doctor\n\n# 5. 隔離された作業シェルに入る（プロンプト先頭に (hako) が付く）\npython3.12 tools/workspace.py enter      # Windows: py -3.12 tools\\workspace.py enter\n\n# 6. 使い方ガイド（HTML）を生成してブラウザで開く\npython tools/recipe.py guide  --recipe recipes/examples/mujoco-turtlebot3-wall-follower.yaml --open\n~~~\n\n`workspace.py enter` は Foundation runtime が未構築の場合は起動しません。先に `recipe.py configure` で Foundation を構築してください。手順 5 で `(hako)` シェルへ入ると、`python` は Foundation venv の Python 3.12 を指します。以後の操作は `python` で構いません。\n\n手順 3 の最後に次のように出れば Foundation は完成です。\n'''
assert text.count(old) == 1, text.count(old)
text = text.replace(old, new)
old = '''続けて `python tools/workspace.py doctor` を実行し、\n`[OK] Foundation Python and Hakoniwa modules are workspace-owned.` と出れば OK です。\n'''
new = '''続けて手順 4 の `workspace.py doctor` で\n`[OK] Foundation Python and Hakoniwa modules are workspace-owned.` と出れば、隔離されたWorkspaceへ入る準備ができています。\n'''
assert text.count(old) == 1, text.count(old)
guide.write_text(text.replace(old, new), encoding='utf-8')
