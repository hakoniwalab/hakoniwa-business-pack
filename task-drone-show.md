# バーチャルドローンショー統合タスク

## 位置づけ

ドローンショーの制作、計画、検証、実行に関する汎用機能は、
[`hakoniwa-drone-show`](https://github.com/hakoniwalab/hakoniwa-drone-show)が所有する。

本書では、`hakoniwa-drone-show`の成果物をBusiness PackのCity World生成、汎用Recipe
基盤および納品物へ統合する作業だけを管理する。ショー機能の詳細計画は
`hakoniwa-drone-show`の[`task.md`](https://github.com/hakoniwalab/hakoniwa-drone-show/blob/main/task.md)
を正本とする。

今後追加するドローンショー固有機能は、Business PackのRecipeから必要になった場合も
原則として`hakoniwa-drone-show`へ実装する。PRO環境を前提とするショー専用Recipe、
experiment、素材、テストも`hakoniwa-drone-show`を正本とする。Business Packへ追加
できるのは、汎用拡張point、City World生成、取得、証跡および納品に必要な統合処理に限る。

```text
hakoniwa-drone-show
  show schema / Formation / Transition / validation
  Show Experience Runner / Show Status / resolved show plan
                         |
                         v
hakoniwa-business-pack
  汎用Recipe基盤 / City World生成 / 配布・納品統合
```

## 責務境界

### hakoniwa-drone-show

- `show.json`、resolved show plan、Show Statusの仕様
- Formation制作、点群化、機体割当、経路計画、事前検証
- LED演出計画
- Show Experience Runnerと開始待機
- 汎用CLI、プレビュー、実行summary

### hakoniwa-business-pack

- 対応版`hakoniwa-drone-show`の取得とrevision固定
- 汎用Recipe基盤とowner非依存の外部拡張point
- PLATEAU City Worldの生成
- 納品Receiptへの入力revision、生成物hash、実行条件の記録
- `nohin-products.bash`を含む納品パッケージへの組み込み

### 他リポジトリ

- `hakoniwa-drone-pro`: Fleet制御、既存Show Runner、Visual State生成
- `hakoniwa-map-viewer`: 自動接続、開始ボタン、状態表示
- `hakoniwa-threejs-drone`: LED Spriteの色・明るさ描画
- `hakoniwa-envsim`: City World、地形、風、環境作用

## 統合要件

- [x] 既存CityドローンショーRecipeを`hakoniwa-drone-show`へ移行する
- [x] Task 0では新機能を追加せず、現在のCityショーを同等に再現する
- [x] `drone_fleet_single_host.py`からショー専用moduleの直接importを削除する
- [x] ICRA／Fleet性能検証経路を維持できる汎用拡張pointを用意する
- [ ] 拡張point未使用時のresolved experiment、Launcher、Runner引数、結果schemaを変えない
- [ ] `hakoniwa-drone-show`を兄弟リポジトリとして扱い、submodule化しない
- [ ] Recipeが対応revisionまたはversionを明示する
- [ ] doctorがリポジトリ、version、CLI、schema互換性を検証する
- [ ] `show.json`から生成されたresolved show planをRecipe workspaceへ配置する
- [ ] City配置変換後のresolved show planをViewerのHTTP rootへ公開する
- [ ] Show Status用の既存`std_msgs/String` PDUとWebBridge設定を生成する
- [ ] LauncherがShow Experience Runnerを開始・停止・監視する
- [ ] ブラウザ準備後の開始要求をRunnerへ接続する
- [ ] Visual State PDUとDrone PROの既存Show Runnerを変更しない
- [ ] ICRA Recipeと性能測定経路を変更しない
- [ ] 200機で統合確認後、既存PROバイナリの範囲で256機を確認する
- [ ] Show Receiptへ各リポジトリrevision、show hash、実行条件を記録する
- [ ] インストール、生成、起動、開始、停止、再実行の手順をFacade文書へ記載する

## Acceptance Test

- [ ] 兄弟ディレクトリの`hakoniwa-drone-show`から計画を生成できる
- [ ] Launcher起動後、開始ボタンを押すまでDroneが離陸しない
- [ ] Viewerがresolved show planと一致するShow Statusだけを受理する
- [ ] City配置、LED、Formation遷移をChromeとSafariで確認できる
- [ ] 200機と256機で同一の統合手順を利用できる
- [ ] Launcher停止後に残留processとportがない
- [ ] 納品パッケージから依存関係と利用手順を再現できる

## 非目標

- PRO専用ドローンショーRecipeのBusiness Pack内維持
- Formation authoringやTransition plannerのBusiness Pack内実装
- `show.json` schemaのBusiness Pack内複製
- Show Experience RunnerのBusiness Pack内複製
- Drone PRO、envsim、Viewerのowner実装の複製
- 300機以上のためのCore再ビルド

## 進捗

- [x] 汎用ショー機能のOwnerを`hakoniwa-drone-show`へ分離
- [x] Business Packの責務を統合層へ限定
- [x] CityドローンショーRecipeを`hakoniwa-drone-show`へ移行
- [ ] `hakoniwa-drone-show`の初期仕様確定
- [ ] Recipeへのversion/revision contract追加
- [ ] LauncherとViewerへの統合
- [ ] 200機E2E
- [ ] 256機E2E
- [ ] 納品Facade文書更新
