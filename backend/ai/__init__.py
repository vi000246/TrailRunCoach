"""AI 問答 (SP-785 backend, SP-786 front end; decided in SP-306 Q1 / Q2): the owner asks about their
training, a card, a chart, a session or a calculator result, and a Claude model answers from the
app's own numbers and rules only.

    config.py   the owner's key (env TRC_ANTHROPIC_API_KEY / its _FILE, or pasted on the settings page
                and sealed), the switch, the model, the daily limit; owner only (tenancy `ai.owner_key`)
    context.py  what is sent: the server's summary (status rules, weekly totals) and the page's
                "AI summary" of the card in view (its format: docs/spec/ai-qa.spec.md), cleaned — no
                GPS track, no coordinates, sizes capped
    qa.py       the prompt, the Messages API call (standard-library HTTPS, no SDK), the answer parsed
                into parts marked app rule / model inference, the cache and the daily count
"""
