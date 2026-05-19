# claude-issueops

> GitHub Issue をセッション間の長期メモリにする Claude Code プラグイン

[English](./README.md) | 日本語

---

## これは何?

Claude Code でコードを書いていると、セッションをまたいだ瞬間に「あの判断、なぜそうしたか」が消える。`claude-issueops` は **GitHub Issue のコメントを永続メモリ層**として使い、決定 (Decision) を Issue に書き戻し、次回起動時に hook で読み直す。

3 つの hook と 1 つの skill が連携して、セッション開始 / 終了 / コンテキスト圧縮 / セッション復帰の 4 つの境界で context を保つ。

---

## 何が困っているか

Claude Code を使っていると、こういう場面に当たる。

- セッションを 1 時間 やって `/quit`、翌日また開く。前日「DB スキーマを正規化しないで JSON カラムに倒す」と決めたのに、Claude は覚えていない。判断の根拠は自分の頭の中だけ。
- 長い作業の途中で context compaction が走る。直後の Claude は「いま何の Issue やってたっけ」を見失う。
- Issue を跨いで学んだこと (例: 「この社内 API は 429 を返したら 5 秒待つ」) を毎回説明し直す。
- 「Issue コメントに決定を書く」を手動でやると、忙しいときに忘れる。フォーマットも揺れて、後から grep できない。

Claude 標準の memory は個人設定 (好み・スタイル) には向くが、**Issue 単位の判断履歴** や **タスクの context** を残す機構ではない。

---

## どう解決するか

`claude-issueops` は Issue を「外部記憶」として扱う。

| いつ | 何が起きるか |
|---|---|
| セッション開始 (最初の prompt) | `UserPromptSubmit` hook が **briefing** を注入。進行中 Issue の一覧 + 現在 Issue の本文 + 過去の Decision コメントを Claude が読める形で渡す。 |
| 作業中、判断が固まった | `/claude-issueops:session-closer --capture` を起動。transcript から Decision を抽出し、確認の上で Issue にコメント投稿。 |
| コンテキスト圧縮 (compaction) | `PreCompact` hook が現在 Issue を state file に保存。圧縮後の最初の prompt で `UserPromptSubmit` が **restore** として再注入。 |
| セッション終了 | `/claude-issueops:session-closer` (引数なし) で summary を投稿 + 横断的な学びを Claude 標準 memory に reference として昇格。 |
| skill を呼び忘れた | `SessionEnd` hook が最小限の summary だけ自動投稿。決定抽出はしない (対話が必要なので)。 |

結果として、判断は **GitHub Issue に常駐**する。後日 `gh issue view 132` で読めるし、次のセッションでも自動で briefing に乗ってくる。

---

## 典型的な 1 日

```bash
# 1. Issue 番号入りのブランチを切る
$ git checkout -b feat/132-auth-rework

# 2. Claude を起動
$ claude
```

最初の prompt で briefing が注入される (Claude にだけ見える、ユーザーには表示されない):

```markdown
<!-- claude-issueops:briefing -->
## Session briefing

### Tier 1 — In-progress issues
- #131 — Add rate-limit retry
- #132 — Auth middleware rewrite
- #135 — Audit log schema

### Tier 2 — Current issue
**#132 — Auth middleware rewrite**
- Parent epic: #99
- Decisions on file: store-tokens-in-redis, rotate-keys-weekly

We need to replace the legacy session-token middleware because legal flagged it
for compliance with the new session-token storage requirements.
```

ポイント: Tier 2 に出るのは Decision の **slug 一覧だけ**。本文 (What / Why / Alternatives / Consequences) は briefing に乗らない。Claude が「`store-tokens-in-redis` の中身を見たい」と判断したら、自分で `gh issue view 132 --json comments` を叩いて取りに行く。briefing は「どこに何があるか」のインデックス、詳細は GitHub Issue 本体が source of truth、という分担。

これで Claude は「いま何の Issue を、どこまで進めたか」と「過去にどんな判断が積み重なっているか (slug ベース)」を理解した状態でセッション開始。

```bash
# 3. しばらく作業して、判断が固まった
> session-closer の capture モードを起動して

# Claude が transcript を読んで Decision 候補を抽出
# → AskUserQuestion で「これを Issue に投稿しますか?」を確認
# → 承認した Decision が #132 に ## Decision: <slug> 形式で投稿される
```

```bash
# 4. 一旦 /quit、翌日 claude --continue で復帰
# → restore が走って、前日の context が戻る

# 5. セッションを終わるとき
> session-closer を起動して

# → 残っている Decision を投稿
# → セッション全体の summary コメントを投稿 (idempotent: 二重投稿しない)
# → cross-issue scope の Decision は Claude memory に reference として昇格
```

翌週、別の Issue で関連する判断が必要になったときも、Claude memory に reference として残っているので自動で参照される。Issue を跨いで context が伝播する。

---

## 機能一覧 (いつ役立つか)

| 機能 | こういうときに効く |
|---|---|
| **session 開始 briefing** | 数日ぶりに復帰して「いま何やってたっけ」が分からないとき |
| **compaction 後 restore** | 長いセッションで context 圧縮が走って Claude が「いま何の Issue?」を忘れたとき |
| **decision capture (skill)** | 「今日の判断は明日も覚えていたい」とき |
| **二段階 dedup** | 同じ skill を何度叩いても、すでに投稿した Decision は二重投稿されない |
| **cross-issue memory 昇格** | Issue を越える学び (社内 API の癖、共通の制約等) を 1 回書いたら他 Issue でも参照されるようにしたいとき |
| **SessionEnd fallback** | skill を呼び忘れても、最小限の summary だけは残る |
| **gh 失敗時の 3 択** | gh コマンドが失敗 (認証切れ等) しても「保存して後で再投稿 / 破棄 / 中断」を選べて、決定が消えない |
| **AmbiguousResolution** | branch から Issue 番号が一意に決まらないとき、AskUserQuestion で候補から選べる |

---

## インストール

```bash
git clone https://github.com/etoyama/claude-issueops.git
claude --plugin-dir ./claude-issueops
```

skill は plugin 名空間付きで `/claude-issueops:<skill>` の形で呼ぶ。marketplace 配信は v0.2 以降の予定。

前提:

- `gh` CLI が認証済 (`gh auth status` が通ること)
- 作業ブランチが `(?:feat|fix|chore|refactor)/<issue番号>-...` の規約に従っているか、対象 Issue に `status:in-progress` ラベルが付いていること

---

## 最小設定

ほとんどの場合、追加設定なしで動く。プロジェクト固有に変えたいときは `.claude/settings.json` に書く:

```jsonc
{
  "issueops": {
    "branch": {
      "issuePattern": "(?:feat|fix|chore|refactor)/(\\d+)-",
      "fallback": "latest-in-progress"
    },
    "memory": {
      "escalate": true
    }
  }
}
```

よく変える項目:

| キー | 既定値 | 用途 |
|---|---|---|
| `branch.issuePattern` | `(?:feat\|fix\|chore\|refactor)/(\d+)-` | branch 名から Issue 番号を抽出する regex。チームの命名規約に合わせて差し替える |
| `branch.fallback` | `latest-in-progress` | regex が刺さらないときの解決方式。`none` にすると skill が「branch から特定できない」で止まる |
| `memory.escalate` | `true` | cross-issue scope の Decision を Claude memory に書くか |

その他のキー (state ファイルの場所、project v2 連携等) は [README.md](./README.md#configuration) 参照。

---

## Decision marker protocol

Decision は Issue のコメントとして、**凍結された形式**で残る。下流ツールが parse するので、形式は固定 (バージョンを跨いで変えない)。

```markdown
## Decision: <kebab-case-slug>

**What:** 一文で「何を決めたか」
**Why:** 理由・制約・動機
**Alternatives considered:**
- 選択肢 A -> 却下理由
- 選択肢 B -> 却下理由
**Consequences:** 得たもの・捨てたもの・将来壊れそうな点
```

ルール:

- `slug` は kebab-case、Issue 内で unique
- 4 フィールド (What / Why / Alternatives / Consequences) がすべて非空
- 同じ slug を再利用するなら、先に古いコメントを手で削除する (skill は上書きしない)

抽出は `^## Decision: (?<slug>[a-z0-9-]+)\s*$` の見出し regex と、直後の `^\*\*What:\*\*` の二段で行う。コードブロックや引用文の中の「## Decision:」は拾わない。

---

## もっと詳しく

- 詳しい仕様 (Hook の動作、SKILL.md contract、State file shape) は [README.md (英語)](./README.md)
- 開発参加: [CONTRIBUTING.md](./CONTRIBUTING.md)
- 変更履歴: [CHANGELOG.md](./CHANGELOG.md)
- L3 検証手順: [VERIFICATION.md](./VERIFICATION.md)

---

## ライセンス

[MIT](./LICENSE) (c) 2026 etoyama.
