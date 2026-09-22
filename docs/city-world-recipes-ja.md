# City World Recipe の選び方

City World には、目的の異なる三つの Recipe がある。生成済みの都市を利用する人、
ブラウザから都市を生成する人、Recipe 自体を検証する人が同じ手順を使わないよう、
最初に目的を選ぶ。

| 目的 | Recipe / 入口 | Foundation | 主な出力 |
| --- | --- | --- | --- |
| 固定した対象範囲から都市アセットを生成する | `plateau-citygml-mujoco-walls` | 不要 | MJCF、GLB、validation、receipt |
| ブラウザで範囲を選び、診断・生成・取得する | `city-world-web-ui` | Core-free Endpoint | Web UI、job ZIP、MJCF、GLB |
| 複数地域の生成可否を回帰検証する | `plateau-city-world-six-regions` | 不要 | 地域別の検証結果 |

## ブラウザで City World を生成する

source checkout を使う場合は、`city-world-web-ui` が唯一の実行入口である。

```bash
python tools/workspace.py enter --workdir /path/to/workspace

# (hako) shell
python tools/recipe.py plan --recipe recipes/examples/city-world-web-ui.yaml
python tools/recipe/city_world_web_ui.py configure
python tools/recipe/city_world_web_ui.py doctor
python tools/recipe/city_world_web_ui.py start --open-browser
```

終了時は同じ Workspace shell で次を実行する。

```bash
python tools/recipe/city_world_web_ui.py stop
exit
```

Worker、Launcher、job、PLATEAU cache は、選択した workdir の次の位置に保存される。

```text
<workdir>/recipes/city-world-web-ui/
  launcher/     # Launcher session、設定、ログ
  runtime/
    jobs/       # 生成済みCity World
    cache/       # 再利用するPLATEAU source cache
```

この Recipe は Hakoniwa Core、`hakopy`、`hako-cmd` を使わない。Foundation は
Core-free PDU Endpoint の native/Python runtime を管理し、PDU Python、PDU JavaScript、
Envsim はrevisionを追跡するsource依存として解決する。

詳細な画面操作は[`city-world-web-ui-guide-ja.md`](city-world-web-ui-guide-ja.md)、
JSON job契約は[`city-world-generation-protocol-ja.md`](city-world-generation-protocol-ja.md)、
Windows配布は[`windows-portable-city-world-workspace-ja.md`](windows-portable-city-world-workspace-ja.md)
を参照する。

## Windows portable package を使う

Windows x64 の利用者は、ZIPを展開し `start-city-world.bat` を実行する。Python、Git、
WSL、Dockerは不要である。portable package は同じ `city-world-web-ui` Recipe runtime を
同梱するため、生成jobとcacheは展開先の
`hakoniwa-business-pack/work/recipes/city-world-web-ui/runtime/` に保存される。

## 固定範囲のオフライン生成

`plateau-citygml-mujoco-walls` は Web UI の導入Recipeではない。固定されたNumazu検証範囲の
アセット生成と、Shibuya旧資産の回帰比較を担当する。Core-free Web UI runtimeを構成したり、
ブラウザを起動したりはしない。
