from __future__ import annotations

import time

from bot_config import DRY_RUN, DRY_RUN_SUMMARIZE, client_oa, secrets


def summarize(title: str, abstract_en: str) -> str:
    system_msg = {
        "role": "system",
        "content": (
            "あなたは、固体地球物理学（地震学・測地学・火山学）の英語アブストラクトを、"
            "深く理解し、正確・簡潔・論文調の自然な日本語で要約する専門家です。"
            "推測や誇張をせず、Abstractに書かれている範囲だけで背景・目的・成果・意義を整理してください。"
            "用語は日本の地球科学分野で一般的な訳語を優先し、表記揺れを避けて統一します。"
            "定訳が確信できない語は、無理にカタカナ化せず英語のまま残します（必要なら簡単な説明を添えます）。"
        ),
    }

    user_msg = {
        "role": "user",
        "content": (
            f"{abstract_en}\n\n"
            f"これは “{title}” というタイトルの論文のAbstractです。\n"
            "【出力形式（Slack mrkdwn）】\n"
            "1. *タイトルの和訳*（論文調で自然な日本語。必要なら初出のみ括弧で英語/カタカナ併記）\n"
            "2. *要点を4点*（背景→目的→成果→意義の順、箇条書き、太字不要）\n"
            "- 各点は1〜2文、重複を避ける（背景に成果を書かない等）\n"
            "- 数式は文章に言い換える（記号は必要最小限）\n\n"
            "【分野判定】\n"
            "固体地球物理学の論文に該当しない場合は要約しない。\n"
            "その場合、固体地球物理学との関連がAbstract内に見えないときは、\n"
            "「これは〇〇分野の△△に関する論文です。」の1文のみ出力。\n"
            "固体地球物理学の論文そのものではないが、地震・断層・火山・測地・地殻変動・地球内部構造・"
            "地表荷重・斜面災害などとの関連がAbstract内に明示されている場合は、\n"
            "上記の分野判定文に続けて、固体地球物理学の読者にとっての意義を \n"
            "「【現象・対象】を【関心・各分野】へ接続する事例として参照できます。」 \n"
            "という形式で簡潔に1文で追記する。\n"
            "この場合も4点要約は行わない。\n"
            "追悼文・編集後記・謝辞・会議報告などの論文ではない文章は、\n"
            "内容を一文で要約する。\n"
            "【欠損判定】\n"
            "要約が提供されていない、または途切れている場合は、\n"
            "タイトルの和訳のみ出力。\n"
            "【自己点検】\n"
            "4点すべて揃っているか／各点が重複していないか確認してから出力。"
        ),
    }

    retries = 3
    for i in range(retries):
        try:
            rsp = client_oa.responses.create(
                model=secrets["openai_model"],
                input=[system_msg, user_msg],
                text={"verbosity": "low"},
            )

            text = (getattr(rsp, "output_text", None) or "").strip()
            if not text:
                raise RuntimeError(
                    f"Empty model output "
                    f"(status={getattr(rsp, 'status', None)} "
                    f"error={getattr(rsp, 'error', None)})"
                )
            return text

        except Exception as e:
            print(f"Retry {i + 1}/{retries} due to API error: {e}")
            time.sleep(5)

    raise RuntimeError("OpenAI API failed after retries.")


def maybe_summarize(title: str, abstract_en: str) -> str:
    """
    Generate summary unless dry-run summarization is disabled.
    This prevents unnecessary OpenAI API calls during dry-run tests.
    """
    if DRY_RUN and not DRY_RUN_SUMMARIZE:
        return (
            "[DRY_RUN] Summary generation skipped. "
            "Set DRY_RUN_SUMMARIZE=true to test OpenAI summarization."
        )

    return summarize(title, abstract_en)
