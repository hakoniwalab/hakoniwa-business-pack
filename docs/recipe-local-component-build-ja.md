# Recipe 固有のネイティブ部品のビルドとインストール（設計メモ）

- 日付：2026-10-27（森・Claude）
- 状態：設計（方向は森の了承済み）。実装はまだ
- 位置づけ：[Foundation と個別 Recipe の設計仕様](hakoniwa-foundation-recipe-design-ja.md) への追加。正本は Business Pack に置く
- きっかけ：箱庭ドローン PRO のお客さん向けに、ArduPilot SITL / ArduPilot Control Link / ArduPilot PID チューニングの Recipe を作る中で、部品ごとの「方言」が見つかった。この機会に正本へそろえる

## 1. 問題

正本には、**Foundation ではない Recipe 固有のネイティブ部品**（例：hakoniwa-drone-pro、hakoniwa-drone-control-adapter-ap、hakoniwa-drone-control-adapter-px4）を、どうビルドしてインストールするかの決まりがない。

- `recipe.py configure` は、Foundation のビルドと git の clone、Python の部品の導入、成果物の有無の確認だけを行う
- 一方で [AGENTS-USER.md](../AGENTS-USER.md) は、隣の部品の `tools/hako.py` を直接走らせて Foundation の要求を迂回することを禁じている
- その結果、各リポジトリがこの穴を自己流で埋めていた（方言）
  - レシピの外で手でビルドする前提（PRO の既存 Recipe）
  - plan の時点で、まだ作られていない成果物を求める（例：`hako.py prepare` で作る MuJoCo を required artifact にしている）
  - 部品に決まった操作の入口がない（adapter-ap、adapter-px4 は `build.bash` のみ）
  - 起動スクリプトがプログラムを何通りもの場所から探す（`/usr/local`、`/opt` まで）
  - リポジトリごとの専用ランナー（robot-arm-pack、Urban にもある。これらも方言であり、見本にはしない）

## 2. 決まったこと

### 2.1 役割の分け方

| 置き場所 | 持つもの |
|---|---|
| Business Pack（正本） | `tools/recipe.py` の流れそのもの（doctor / plan / configure / build / install）。Recipe 固有の部品を、その部品の決まった操作で呼ぶ決まり |
| 部品のリポジトリの `recipes/recipe.py` | 薄い入口。隣の Business Pack を見つけ、Foundation を確かめ、Recipe を渡して正本の `tools/recipe.py` を呼ぶだけ。流れを持たない |
| 各部品の `tools/hako.py` | 決まった操作の言葉で応える。ビルドの中身は部品が持つ |

- 流れの本体を部品側に書くと、リポジトリごとに別の方言に育つ。本体は Business Pack に 1 つだけ置く
- 公開の Business Pack は、非公開の部品（PRO など）のソースの中身を知らない。部品の `tools/hako.py` という決まった口を呼ぶだけ

### 2.2 部品の入口（`<component>/recipes/recipe.py`）

```text
python recipes/recipe.py <doctor|plan|configure|build|install|launch> --recipe recipes/<id>.yaml
```

1. 隣の Business Pack を見つける（`HAKONIWA_BUSINESS_PACK_ROOT`、なければ `../hakoniwa-business-pack`）
2. Business Pack の版を確かめる（このメモの決まりを知らない古い版なら、止めて更新を案内する）
3. Business Pack の Workspace（`tools/workspace.py`）の中で、正本の `tools/recipe.py` に同じ引数で渡す
4. Foundation が SATISFIED でなければ、正本の決まりどおり止める（設計仕様 14 章）

### 2.3 部品の決まった操作（`tools/hako.py`）

設計仕様 13 章の言葉に合わせる。

| 操作 | してよいこと |
|---|---|
| doctor | 足りないものを確かめて知らせるだけ。直さない |
| configure | 部品の設定（manifest）を確かめて決める。install 先を受け取る |
| build | configure で決めた構成でビルドする |
| test | ビルドの木の上で部品のテストをする |
| install | 作った成果物を、受け取った install 先へ置く。Receipt を残す |
| smoke | install 済みの成果物が install 先から使えることを軽く確かめる |

- install 先は正本が渡す（Recipe のワークスペースの下）。部品の中（`.hako/install`、`install/`）やシステムのディレクトリに置かない
- 配布物の取得（MuJoCo の SDK、ArduPilot のサブモジュールなど）は、部品の操作の中で行ってよい。ただし plan で「何を、どこから取るか」を見せ、configure の明示の実行で取る（設計仕様 16 章：ネットワークからの取得は明示の確認が要る）
- 今の `prepare`（PRO の MuJoCo の準備）は configure に含める（6 つの言葉は増やさない）

### 2.4 Recipe の書き方（足すだけ）

- `recipe_local_requirements_schema_version: 2` を足す。版 2 のときだけ、部品ごとに「部品の操作で作って置く」書き方を許す（例）

  ```yaml
  recipe_local_requirements_schema_version: 2
  recipe_local_requirements:
    hakoniwa-drone-control-adapter-ap:
      root: {default_path: ../hakoniwa-drone-control-adapter-ap, override_env: HAKO_AP_ADAPTER_ROOT, relative_to: recipe_repository}
      source: {type: git, url: https://github.com/hakoniwalab/hakoniwa-drone-control-adapter-ap.git, revision: <固定の版>}
      build:
        contract: hako_cli          # 部品の tools/hako.py を決まった操作で呼ぶ
        config: config/build/<構成>.yaml
      required_artifacts:           # install の後に確かめるもの（plan の時点では求めない）
        - {path: ..., kind: executable, phase: installed}
  ```

- plan の時点で求めるのは「ソースにあるもの」だけ。ビルドや準備で作られるものは `phase: installed` として、install の後に確かめる
- 版 1 の Recipe は、今までとまったく同じ読み方をする

### 2.5 ソース納品（git の情報がない配布物）

- 配布物の根に目印のファイル `.hakoniwa-repository-root` を置く。今は `recipe.py` が実際に見ているだけで、文書に書かれていない（このメモで正本に入れる）
- 納品の道具（例：PRO の `tools/nohin/nohin.bash`）は、拡張子で絞り込んだ後に目印を書く（絞り込みで拡張子のないファイルが消えるため）

## 3. 互換性とデグレの防ぎ方

**方針：今の Recipe は 1 文字も直さずに、今までどおり動く（足すだけ、変えない）**

- 項目は足すだけ（版 2 にだけ新しい書き方）。コマンドも足すだけ（今の doctor / plan / configure / launch の動きは変えない）
- 古い道具（各リポジトリの専用ランナー、PRO の今の起動スクリプト）はすぐに消さない。新しい形へ移してから段階的に外す

確かめ方：
1. **plan の出力を比べる**：変更の前に、`recipes/examples/` の Recipe 全部で `recipe.py plan` の出力を記録し、変更の後に一字一句同じであることを確かめる（ビルドも起動もしないので全部まとめて流せる）
2. **既存のテストを全部通す**：`validate_recipes`、`recipe.py`・`foundation.py` のテスト
3. **実際に 1 本動かす**：検証済みの代表の Recipe（`mujoco-turtlebot3-wall-follower`）を、configure から起動まで流す

## 4. 未決事項

なし（2026-10-27 に決着。下記）

### 決着したもの（2026-10-27 森）

- **Python だけの Recipe（PID チューニング）**：問題にしない。今の PID チューニングの Recipe は `foundation_contract: required`（core-pro の Python バインディング）として書いてあり、Foundation を 1 回作れば足りる。お客さんは SITL などのために Foundation をどのみち作るので、別の種類は足さない
- **部品のリポジトリに置いた Recipe の検査**：後回し。今は納品を優先する
- **`prepare` の扱い**：configure に含める（6 つの言葉は増やさない）
- **Control Link と PX4 のアダプター**：今は PX4 のアダプターを Recipe の依存として宣言するだけにする（PRO は変えない）

## 5. 進め方（お客さんの ArduPilot の Recipe に効く順）

1. Business Pack：このメモの決まりを実装する（版 2 の読み方、build / install の流れ、目印の文書化、部品の入口から渡された Recipe の検査）。デグレの確かめは 3 章のとおり
2. hakoniwa-drone-pro：`recipes/recipe.py`（薄い入口）、`tools/hako.py` の不足（ArduPilot の機体サービスを install する、Mac だけのライブラリのつなぎ直しを build / install の中へ、install 先を受け取る、smoke）、ソース納品に目印を入れる
3. hakoniwa-drone-control-adapter-ap、hakoniwa-drone-control-adapter-px4：`tools/hako.py` を置く（今の `build.bash` と SITL のビルドを呼ぶ薄いもの。Python の部品は宣言する）
4. ArduPilot の 3 つの Recipe（SITL、Control Link、PID チューニング）を版 2 で書き、まっさらな環境（ソース納品＋Business Pack の clone）で Mac → WSL2 の順に確かめる

1 件ずつ小さく進め、各段階をまっさらな環境で確かめてから次へ進む。
