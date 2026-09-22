# City World Web UI 利用手順

## 1. この画面でできること

City World Web UIは、地図で選択した範囲のPLATEAUデータを診断し、
Hakoniwaで同じworld-frameを共有する次の成果物を生成するローカルWebアプリケーションである。

- 表示用: `city-world.glb`
- 物理シミュレーション用: `city-world.xml`（MuJoCo MJCF）
- 検証結果: `dataset-validation.json`
- 生成条件と来歴: `city-world-receipt.json`

ローカルのCityGMLファイルをブラウザへアップロードする画面ではない。
範囲診断では公式PLATEAU APIのcatalogを参照し、`City Worldを生成`したときだけ
対象のCityGMLとテクスチャを取得する。

プロトコル、生成処理、Collider分類などの詳細仕様は
[`city-world-generation-protocol-ja.md`](city-world-generation-protocol-ja.md)を参照する。

## 2. 前提

### 2.1 Windows Portable版

Windows x64向けに作成済みのportable packageを利用する場合は、Business Packの開発Workspaceを構築する必要はない。

```text
ZIPを展開
  -> start-city-world.bat
  -> ブラウザ起動
```

Python、Git、WSL、Dockerの追加インストールも不要である。portable packageの作成方法と配布物の構成は[`windows-portable-city-world-workspace-ja.md`](windows-portable-city-world-workspace-ja.md)を参照する。

### 2.2 Source checkoutから起動する場合

コマンドは`hakoniwa-business-pack`のルートで実行する。

- Business PackのWorkspace環境が構成済みであること
- `../hakoniwa-envsim/tools/hako.py`が存在すること
- `../hakoniwa-pdu-javascript/src/index.js`が存在すること
- PLATEAU API、CityGML、地図データへ接続できること
- 既定のTCP port `54210`とHTTP port `8008`が空いていること

別の配置を使う場合、Envsimは`HAKONIWA_ENVSIM_ROOT`、PDU JavaScriptは
`HAKONIWA_PDU_JAVASCRIPT_ROOT`でルートを指定できる。

Workspace自体の準備方法は
[`getting-started-ja.md`](getting-started-ja.md)と
[`hakoniwa-workspace-environment-ja.md`](hakoniwa-workspace-environment-ja.md)を参照する。

## 3. 起動

通常は、WorkerとWeb serverをまとめて管理するLauncherを使う。

```bash
python3 tools/workspace.py run -- \
  python3 -m tools.remote_operation.city_world.launcher start \
  --parallel-workers 4 \
  --dem-parallel-workers 2 \
  --terrain-spacing-m 2 \
  --open-browser
```

起動に成功すると`Web UI : http://127.0.0.1:8008/`が表示される。
`--open-browser`で開かなかった場合は、このURLを手動で開く。

画面上部が`Worker接続済み`になれば操作できる。

## 4. City Worldを生成する

### 4.1 対象範囲を選ぶ

地図をクリックするか、中心マーカーまたは青い矩形をドラッグして中心を決める。
矩形四隅のハンドル、または入力欄で東西・南北の範囲を変更できる。

`N/S half extent`と`E/W half extent`は中心から各方向への距離である。
たとえば両方を`100 m`にすると、生成範囲は`200 m x 200 m`になる。
指定可能な値はそれぞれ`10–1000 m`である。

### 4.2 生成条件を選ぶ

初回は既定値のままでよい。

- `Building Physics Level`: 建物Colliderの詳細度。`0`が最軽量、既定の`3`が最詳細。
  Visualとテクスチャの詳細度には影響しない。
- `DEM未被覆領域`: 通常は`停止する（厳密・既定）`を使う。海や河川を含むためDEMが
  矩形全体を覆わない場合に限り、`標高0 mで補完（水面向け）`を検討する。
- Collider削減: すべてOFFが安全側の既定値。下の項目ほど削減が強く、
  `実験的`または最大5 cmの近似を含むため、必要性を確認して有効にする。

### 4.3 診断して生成する

1. `Capabilityを診断`を押す。
2. Building、Terrain、Road、Road markings、Bridgeの診断結果と推定取得量を確認する。
3. `生成候補あり`になったら`City Worldを生成`を押す。
4. `READY`または画面の`Generate成功`表示まで待つ。

診断だけではCityGML本体を取得しない。範囲や生成条件を変更した場合は、もう一度診断する。
生成中は画面に処理phaseと進捗が表示され、`生成をキャンセル`で中止できる。

## 5. 結果を確認・取得する

生成後、`生成結果`からjobを選択する。新しい結果が先頭に表示される。

- `Visual`: 表示用GLBを表示する。
- `Collider`: MJCFから作った確認用wireframe GLBを表示する。
- `3D Viewer`: 選択したレイヤーを画面下部に表示する。両方を重ねることもできる。
- `Download ZIP`: 検証済み成果物をまとめて取得する。
- `生成結果を削除`: 選択jobのZIP、GLB、MJCF、中間生成物を削除する。

Collider GLBは確認表示用であり、物理判定の正本は`city-world.xml`である。
`生成結果を削除`しても、再利用される共有CityGML cacheは削除されない。

ZIPには次の固定パスで成果物が入る。

```text
visual/city-world.glb
physics/city-world.xml
validation/dataset-validation.json
receipt/city-world-receipt.json
```

サーバー上ではjobごとの全成果物を次に保存する。

```text
work/remote-operation/city-world-worker/jobs/<job-id>/
  build/world/
    city-world.glb
    city-world.xml
    dataset-validation.json
    city-world-receipt.json
  artifacts/
    city-world-<job-id>.zip
  viewer/
    city-world.glb
    city-world-colliders.glb
  generation.log
```

共有cacheは`work/remote-operation/city-world-worker/cache/plateau-citygml/`に保存する。
Launcherを停止してもjobと共有cacheは残る。

## 6. 状態確認と停止

```bash
python3 tools/workspace.py run -- \
  python3 -m tools.remote_operation.city_world.launcher status
```

```bash
python3 tools/workspace.py run -- \
  python3 -m tools.remote_operation.city_world.launcher stop
```

このLauncherはCoreを必要としない`activate-only`構成であり、操作に
`hako-cmd start/stop/reset`は使わない。

Launcherのsession、設定、ログは次に保存する。

```text
work/remote-operation/city-world-launcher/
  city-world.launch.json
  launcher-session.json
  logs/
    worker.out
    worker.err
    web.out
    web.err
```

## 7. 主な起動オプション

| オプション | 既定値 | 用途 |
|---|---:|---|
| `--parallel-workers` | `4` | source取得と独立component生成の並列数（1–16） |
| `--dem-parallel-workers` | `2` | DEM source抽出のprocess数（1–4） |
| `--terrain-spacing-m` | `2` | 地形grid間隔。`2`、`5`、`10`、`auto` |
| `--max-download-gib` | `8.0` | 1回の生成で許可する推定download量の上限 |
| `--worker-port` | `54210` | WorkerのWebSocket port。現在の同梱UIでは既定値を使う |
| `--web-port` | `8008` | Web UIのHTTP port |

広い範囲では、まず`--terrain-spacing-m auto`を使う。`auto`は推定sample数に応じて
`2 m`、`5 m`、`10 m`から選択する。並列数や地形間隔を変える場合は、Launcherを一度停止して
新しいオプションで起動し直す。

```bash
python3 tools/workspace.py run -- \
  python3 -m tools.remote_operation.city_world.launcher start \
  --parallel-workers 6 \
  --dem-parallel-workers 4 \
  --terrain-spacing-m auto \
  --open-browser
```

## 8. トラブルシュート

### Workerへ接続できない

`launcher status`を確認し、次のログを見る。

```text
work/remote-operation/city-world-launcher/logs/worker.err
work/remote-operation/city-world-launcher/logs/web.err
```

HTTP port `8008`が使用中なら、停止後に`--web-port`を変更して起動する。
同梱UIの接続先WebSocketは現在`127.0.0.1:54210`であるため、`--worker-port`だけを
変更しても画面は接続できない。Worker側の`54210`を空けてから再起動する。

### 診断が`SELECTION_UNAVAILABLE`になる

選択範囲に利用可能なPLATEAUデータがない。中心または範囲を変更して再診断する。
個別featureの404は、そのfeatureが対象区画に存在しないという診断結果であり、必ずしも通信障害ではない。

### 推定download量の上限で拒否される

まず範囲を小さくする。ディスク容量と通信量を確認したうえで必要な場合だけ、
Launcher起動時の`--max-download-gib`を明示的に変更する。

### DEM未被覆で停止する

海・河川の水面を含むことが原因と確認できる場合は、画面で
`標高0 mで補完（水面向け）`を選び、診断からやり直す。内陸の欠損まで0 mになる可能性が
あるため、単に生成を通す目的では使用しない。

### 生成に失敗する

画面の`通信ログ`とjobの`generation.log`を確認する。jobの記録は次にある。

```text
work/remote-operation/city-world-worker/jobs/<job-id>/
```

詳細なstatus遷移、生成policy、手動2-terminal起動、成果物contractは
[`city-world-generation-protocol-ja.md`](city-world-generation-protocol-ja.md)を参照する。
