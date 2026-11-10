from jobalert.notify.telegram import AlertContent, format_alert, tailor_keyboard


def _content(**kw):
    base = dict(job_id=7, company="Upwind", title="AI Engineer <Junior>", location="Tel Aviv",
                workplace_type="hybrid", url="https://x.example/job?a=1&b=2")
    base.update(kw)
    return AlertContent(**base)


def test_basic_alert_fields_and_escaping():
    text = format_alert(_content())
    assert "🏢 <b>Upwind</b>" in text
    assert "AI Engineer &lt;Junior&gt;" in text
    assert "Tel Aviv · Hybrid" in text
    assert "עדיין בלי AI" in text
    assert 'href="https://x.example/job?a=1&amp;b=2"' in text
    assert "Borderline" not in text


def test_scored_borderline_alert():
    text = format_alert(_content(score=72, borderline=True, borderline_gaps="חסרות 2 שנות ניסיון ב-SQL",
                                 reason_he="מתאים בזכות ניסיון ניהולי"))
    assert "⭐ ציון: 72/100" in text
    assert "⚠️ <b>Borderline</b>: חסרות 2 שנות ניסיון ב-SQL" in text
    assert "📝 מתאים בזכות ניסיון ניהולי" in text


def test_test_flag_and_button():
    assert format_alert(_content(is_test=True)).startswith("🧪")
    kb = tailor_keyboard(7)
    button = kb["inline_keyboard"][0][0]
    assert button == {"text": "התאם קו״ח", "callback_data": "tailor:7"}


def test_email_job_without_score():
    text = format_alert(_content(email_source="LinkedIn"))
    assert "📬 ממייל של LinkedIn · בלי ציון AI" in text
    assert "עדיין בלי AI" not in text


def test_tailor_button_is_off_by_default():
    from jobalert.notify.telegram import TelegramClient
    sent = []
    for enabled, expected in [(False, None), (True, tailor_keyboard(7))]:
        tg = TelegramClient("t", "c", tailor_button=enabled)
        tg.send_message = lambda text, markup=None: sent.append(markup) or 1
        tg.send_alert(_content())
        assert sent[-1] == expected
        tg.close()
