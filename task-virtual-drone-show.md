# 任意都市バーチャルドローンショー実現タスク

## 目的

PLATEAUから生成した任意都市のVisual World / Physics Worldを使い、次の3段階を順番に実現する。

1. PS4コントローラで、1機のMuJoCoドローンを任意都市で飛行する
2. 同じ任意都市で、100機のMuJoCoドローンによる`HAKONIWA`ドローンショーを実行する
3. ブラウザで指定した風の領域・風向・風速を、単機飛行と100機ショーの双方へ反映する

最終的な利用者体験は次とする。

```text
地図で都市と範囲を選択
  -> City Worldを生成
  -> 単機飛行またはDrone Showを選択
  -> MuJoCo内で都市と衝突しながら飛行
  -> 必要に応じて地図上へ風領域を描画
  -> 風による機体偏差をブラウザで観察
```

## 完了の定義

本タスク全体は、利用者がBusiness Packの標準Launcherとブラウザ画面を使い、任意都市について次を再現できた時点で完了とする。

- 1機をPS4コントローラで離陸・移動・着陸できる
- ドローンがMuJoCo内の地形・建物・橋梁Colliderへ衝突する
- 100機が同じ都市内で離陸し、`HAKONIWA`を形成できる
- ドローン同士は物理衝突しない
- ブラウザで指定した局所風により、単機と100機の双方が実際に流される
- Visual World、Physics World、地図、ドローン位置、風領域の座標が一致する
- 終了後にLauncher所有process、port、一時sessionが残らない

単にThree.js上へドローンを表示するだけでは完了としない。各機体の運動状態がMuJoCo runtime内で進行し、都市Colliderとの接触がMuJoCo contactとして成立することを必須とする。

## 既存資産と改修方針

### 単機飛行

既存の次のRecipeを任意都市対応へ一般化する。

```text
recipes/examples/drone-single-mujoco-shibuya-map-gamepad.yaml
tools/recipe/drone_shibuya_gamepad.py
```

現状は、渋谷の固定GLB、固定MuJoCo XML、固定Map Viewer原点を前提としている。新しい類似Recipeを追加するのではなく、このRecipeの責務を「選択済みCity Worldを使う単機Gamepad飛行」へ拡張する。既存利用者への移行方法が必要な場合は、旧commandまたは渋谷presetを互換入口として残す。

### 100機Drone Show

ICRA性能測定用Recipeは変更しない。

```text
recipes/examples/drone-fleet-single-process-scaling.yaml
recipes/examples/drone-fleet-multi-process-scaling.yaml
recipes/examples/drone-fleet-multi-host.yaml
recipes/experiments/drone-fleet-performance/
```

Drone Showの改修対象候補は、ICRAとは独立してHAKONIWA formationを既に実行する次のRecipeとする。

```text
recipes/examples/drone-fleet-single-host.yaml
tools/recipe/drone_fleet_single_host.py
tools/recipe/drone_fleet_runtime.py
```

新規Recipeは原則追加しない。既存のFleet Management、show scenario、`HAKONIWA` formation、Launcher、Visual State Publisher、WebBridgeを再利用し、physics backendをMuJoCo対応へ拡張する。

### 任意都市生成

City Worldの正本は、既存のCity World Workerが生成した成果物とする。

```text
visual/city-world.glb
physics/city-world.xml
receipt/city-world-receipt.json
validation/dataset-validation.json
```

飛行Recipe側でPLATEAU変換処理を複製しない。ブラウザで選択した生成結果、または明示指定したCity World artifactをRecipe workspaceへmaterializeし、Receiptとhashを記録する。

### 風

次の既存資産を優先して再利用する。

- `hakoniwa-envsim` Creatorの矩形・円形zone編集
- `hakoniwa-envsim` EnvAssetの位置検索
- `hako_msgs/Disturbance`の`d_wind`
- Droneごとの既存Disturbance PDU channel
- ブラウザ/PDU通信に利用できる既存JavaScript bindingとWebBridge

ローター故障データと風外乱を混同しない。風は`Disturbance.d_wind`として扱い、故障情報用PDUや故障contextへ格納しない。

## 重要な不変条件

### 座標

- City World生成時のquery centerをlocal world原点の正本とする
- City World Receiptに記録された座標系と原点を使用する
- Map Viewer、GLB、MJCF、Drone Visual State、Envsim zoneで同じlocal ENU契約を共有する
- 緯度経度とMuJoCo座標の変換を各componentへ重複実装しない
- 風向の意味を明記する。「風が吹いてくる方角」と「速度ベクトルが向かう方角」を混同しない
- 風速の単位は`m/s`、位置と範囲の単位は`m`とする

### Physics

- ドローンの運動はMuJoCo内で実現する
- 地形・建物・橋梁とのcollisionは有効にする
- ドローン同士のcollisionだけを無効にする
- 地面とのcollisionまで無効になるような一括設定は禁止する
- visual meshをphysicsの正本として扱わない
- City WorldのPhysics LevelとCollider削減modeをReceiptから追跡できるようにする

### Recipe

- ICRA性能測定Recipeと測定結果を変更しない
- 類似Recipeを増やさず、既存の単機RecipeとFleet Recipeを一般化する
- owner repositoryの生成物を手編集しない
- 生成物は`work/`配下へ置き、同じ選択に対して無制限にrun directoryを増やさない
- Foundation、Launcher、doctor、stopの既存契約を維持する

## Drone間非衝突の設計契約

MuJoCoの`contype` / `conaffinity`を明示し、少なくとも次のcollision matrixを実現する。

| 組み合わせ | collision |
|---|---|
| Drone - terrain | 有効 |
| Drone - building | 有効 |
| Drone - bridge | 有効 |
| Drone - Drone | 無効 |

mask値そのものはPhase 0で既存MJCFを調査して決める。例えばDroneとCityを別collision groupへ分離できるが、MuJoCoのpair判定、既存worldのmask、機体内geom同士の扱いを数値テストで確認せずに値を固定しない。

最低限、次を自動試験する。

1. 2機を重なる軌道へ置いてもDrone間contactが生成されない
2. 同じ機体を地形へ降下させるとcontactが生成される
3. 建物壁へ移動させるとcontactが生成される
4. 橋梁データがある場合、橋面へ着地でき、橋下空間を通過できる
5. 100機化してもcollision maskが全機へ同じ規則で適用される

## Phase 0: 現行runtimeの事実確認

実装前に、以下をコードと最小実行で確定する。

- [ ] `drone-single-mujoco-shibuya-map-gamepad`の機体運動が実際にMuJoCo内で進行しているか確認する
- [x] 固定渋谷world、Drone model、Map Viewer原点がどこで結合されているか一覧化する
- [x] City Worldの`physics/city-world.xml`をDrone modelへinclude/compositionできる境界を確認する
- [x] 既存Drone geom、terrain、building、bridgeの`contype` / `conaffinity`を記録する
- [ ] `drone-fleet-single-host`が現在使用するdynamics backendと、MuJoCoを使用していない範囲を明確にする
- [ ] 100機を1つのMuJoCo modelへ置く方式と、複数MuJoCo instanceへ分割する方式を比較する
- [ ] City Colliderを100回複製せず共有できる構成を優先して検討する
- [ ] Fleet ManagementがMuJoCo stateをcommand/observationへ接続できるか確認する
- [ ] EnvAssetが複数Droneの位置を読み、Droneごとの`Disturbance`へ書けることを確認する
- [ ] Drone dynamicsが`Disturbance.d_wind`を実際の力学へ反映することを単機で確認する
- [ ] Creatorのzone編集結果をruntime中に更新する既存経路の有無を確認する
- [ ] ブラウザ、PDU、EnvAsset間の更新ownershipと更新周期を決める

### Phase 0 完了条件

- MuJoCo内の単機および100機構成を図示できる
- City Worldを結合するownerと生成場所が一意に決まっている
- Drone間非衝突maskの具体値と根拠が決まっている
- 風zoneを編集してから各Droneへ反映されるまでのPDU flowが決まっている
- owner repositoryごとの変更範囲を説明できる

## Phase 1: 任意都市での単機Gamepad飛行

### 1.1 Recipeの一般化

- [x] 渋谷固定GLB/MJCF/originを、選択済みCity World artifactへ置き換える
- [x] City World artifactのpath、hash、origin、bboxを検証し、元Receiptを証跡へ記録する
- [x] City Worldが未生成、破損、座標不一致の場合はconfigure/doctorで拒否する
- [ ] 渋谷presetを選んだ場合も同じ一般化経路を通す
- [x] 既存Recipe IDとrunner名を互換入口として維持し、titleと主責務を任意都市へ一般化する
- [x] portalにsource job、緯度経度、範囲、生成結果、Collider数を表示する

### 1.2 MuJoCo composition

- [x] City World PhysicsとDrone modelを別の正本から最終worldへ統合する
- [x] Drone modelをMuJoCo内のfree bodyとしてロードする
- [ ] PS4 controller入力がMuJoCo droneへ伝わることを確認する
- [x] City Worldの高度offsetをGPS基準とspawn高度へ反映する
- [x] Drone-City collisionを有効にする
- [x] 将来のFleetと共通化できるDrone間非衝突maskを単機段階から設定する
- [x] configure時に最終MuJoCo XMLをMJBへコンパイルし、同一MuJoCo版で再ロード検証する
- [x] Drone PROをXML/MJBの両形式に対応させ、runtimeはMJBを選択する

### 1.2.1 現在の実行入口

```bash
python3 tools/recipe/drone_shibuya_gamepad.py configure \
  --city-world work/remote-operation/city-world-worker/jobs/<JOB_ID> \
  --spawn-altitude-m 20

python3 tools/recipe/drone_shibuya_gamepad.py doctor
python3 tools/recipe/drone_shibuya_gamepad.py start
python3 tools/recipe/drone_shibuya_gamepad.py open-viewer
```

`--city-world`にはworker job、`build/world`、`city-world-receipt.json`、
`city-world.xml`、`city-world.glb`のいずれかを指定できる。省略時は従来の
固定渋谷presetを利用する。City World jobを削除した場合、file-backedな
MuJoCo XMLの再生成はできないため、別jobを指定して再configureする。runtimeは
configure時に生成・再ロード検証した`drone.mjb`を使用するため、起動ごとに大規模XMLを
再コンパイルしない。XMLとMJBのhash、MuJoCo version、使用libraryは
`validation/materialization.json`へ記録される。MJBはMuJoCo version-boundなので、
Drone PRO側のMuJoCoを更新した場合は必ず再configureする。

現在のcollision maskは次である。

```text
City  : contype=1, conaffinity=0
Drone : contype=2, conaffinity=1
```

MuJoCoのpair判定ではCity-Droneが有効、Drone-Droneが無効になる。人工fixture、
実City Worldの合成、XMLからのMJB生成、MJB再ロード検証、MJB対応Drone PRO Serviceを
含むLauncher readyまで自動・実機binaryで確認済み。
PS4/PS5入力、飛行、接触の最終確認は利用者によるAcceptance Testとする。

### 1.3 Acceptance Test

- [ ] 平坦な都市で離陸、前後左右移動、旋回、着陸ができる
- [ ] 建物へ衝突し、すり抜けない
- [ ] 起伏のある都市で地形高度と接触位置が一致する
- [ ] 橋梁がある都市で橋上/橋下の契約を確認する
- [ ] Map Viewer上の位置とMuJoCo/Three.js上の位置が許容誤差内で一致する
- [ ] 利用者が任意の都市を選択し、完成版で実際に飛行できる

### Phase 1 完了条件

利用者がCity Worldを選び、PS4コントローラで1機を飛ばし、都市Colliderとの衝突を目視・数値の両方で確認できる。

## Phase 2: 任意都市での100機Virtual Drone Show

### 2.1 既存Fleet RecipeのMuJoCo化

- [ ] `drone-fleet-single-host`へCity World artifact選択を追加する
- [ ] 従来backendとMuJoCo backendの責務差を整理する
- [ ] 100機すべての運動状態をMuJoCo内で進行させる
- [ ] Fleet Managementのtakeoff、move、formation、hold、finishを維持する
- [ ] 既存の`HAKONIWA` formation generatorを再利用する
- [ ] ICRA performance Recipeからコードや設定を分岐コピーしない
- [ ] ICRA測定条件と結果を変更しない

### 2.2 100機のPhysics契約

- [ ] City Colliderを可能な限り1つのMuJoCo worldで共有する
- [ ] Drone間collisionを無効化する
- [ ] 各DroneとCity Colliderのcollisionを有効化する
- [ ] Drone ID、MuJoCo body ID、Fleet ID、PDU asset名をdeterministicに対応させる
- [ ] 100機のinitial placementがCity Collider内や同一点にならないよう検証する
- [ ] MuJoCo model load時間、step時間、memory、geom/contact数をReceiptへ記録する
- [ ] 実時間進行を必須条件にせず、見栄えと安定性を優先する

### 2.3 Showと表示

- [ ] 100機が段階的または一斉に離陸する
- [ ] 都市内の安全な表示高度へ移動する
- [ ] `HAKONIWA`文字を形成する
- [ ] 一定時間holdする
- [ ] 終了または着陸まで実行する
- [ ] ブラウザで都市GLBと100機を同時表示する
- [ ] 文字が視認できるcamera presetを用意する
- [ ] 実行状態と完了理由をexecution summaryへ記録する

### Phase 2 Acceptance Test

- [ ] 100機すべてがMuJoCo bodyとして存在する
- [ ] 100機すべてがFleet Management commandへ追従する
- [ ] Drone同士を交差させても相互contactが発生しない
- [ ] 代表Droneを建物へ向けるとCity contactが発生する
- [ ] `HAKONIWA`がブラウザで明瞭に読める
- [ ] Showを3回実行し、欠落Drone、異常終了、残留processがない

### Phase 2 完了条件

任意都市の同一City World上で、100機のMuJoCo droneが`HAKONIWA`を形成し、Drone間非衝突とCity衝突を両立する。

## Phase 3: ブラウザ編集可能な局所風

### 3.1 ブラウザ操作

- [ ] 地図上で矩形の風領域を作成できる
- [ ] 風領域をドラッグ移動できる
- [ ] handle操作で領域をリサイズできる
- [ ] 風向と風速を入力できる
- [ ] 風向を矢印、風速を数値と色で表示する
- [ ] zoneを選択、更新、無効化、削除できる
- [ ] City World選択bbox外のzoneをどう扱うか明示する
- [ ] 単機/Fleetの実行中にも安全な更新点で反映できる

### 3.2 Envsim/PDU連携

- [ ] ブラウザzoneをEnvsim Creator modelへ変換する
- [ ] JSON schemaで座標、範囲、風向、風速、versionを検証する
- [ ] EnvAssetが各Drone位置から該当zoneを検索する
- [ ] 各Droneの`Disturbance.d_wind`へ風速vectorを書き込む
- [ ] zone外ではbase windまたは無風へ戻る
- [ ] zone重複時の優先順位・加算・absolute規則を明記する
- [ ] 更新sequenceを持ち、古いbrowser commandで新しい風場を上書きしない
- [ ] 100機へのfan-outがPDU周期を圧迫しないことを確認する

### 3.3 風のPhysics検証

- [ ] 無風時のhover driftをbaselineとして記録する
- [ ] 単機を一定風zoneへ進入させ、期待方向へ偏差が生じることを確認する
- [ ] zoneを出た後に外乱値が戻ることを確認する
- [ ] 風向を反転すると偏差方向も反転することを確認する
- [ ] 同時刻にzone内外のDroneが異なる外乱を受けることを確認する
- [ ] 100機のformationが風で崩れる様子を可視化する
- [ ] controllerの補償で完全に相殺される場合は、風PDU値と機体応答を別々に記録する

### Phase 3 Acceptance Test

- [ ] ブラウザで風領域を作り、単機を流せる
- [ ] 同じ操作で100機Showの一部または全部を流せる
- [ ] zone境界とDrone位置を地図/3D上で対応付けられる
- [ ] 各Droneへ適用された風vectorをinspectできる
- [ ] 風を削除すると無風状態へ戻る
- [ ] runtime中の更新、停止、再起動で不整合や残留状態がない

### Phase 3 完了条件

同じブラウザ操作とEnvsim assetを単機RecipeとFleet Recipeが共有し、地図上の局所風がMuJoCo droneの運動へ再現可能な形で反映される。

## Receipt / provenance

各実行について最低限、次を保存する。

```json
{
  "city_world": {
    "artifact": "...",
    "sha256": "...",
    "origin": {"latitude": 0.0, "longitude": 0.0},
    "bbox": {},
    "physics_level": 0,
    "collider_count": 0
  },
  "drone_runtime": {
    "backend": "mujoco",
    "drone_count": 1,
    "drone_drone_collision": false,
    "drone_city_collision": true,
    "collision_masks": {}
  },
  "show": {
    "scenario": "HAKONIWA",
    "status": "not-run"
  },
  "wind": {
    "model_sha256": null,
    "zone_count": 0,
    "unit": "m/s"
  }
}
```

## 非目標

- ICRA性能測定の再実施または測定Recipeの変更
- Drone同士の衝突回避アルゴリズム
- Drone同士の空力干渉
- プロペラ後流のCFD再現
- 都市全域の気象予報
- 建物による風の自動推定
- 100機を超えるscale検証
- 複雑なShow演出、音楽同期、複数文字animation
- PX4/ArduPilotを用いたSITL化
- 橋梁や建物のVisual/Physics変換精度そのものの改善

## 実装順序

後続Phaseを先行実装しない。

```text
Phase 0: runtime・座標・collision・wind PDU契約を確定
  -> Phase 1: 任意都市で単機MuJoCo飛行
       -> Phase 2: 同じ構成を100機Fleetへ拡張
            -> Phase 3: 単機/Fleet共通の局所風を追加
```

特にPhase 1を完了する前に100機化しない。単機でCity World composition、MuJoCo collision、座標一致、Disturbance入力の境界を確定してからFleetへ展開する。
