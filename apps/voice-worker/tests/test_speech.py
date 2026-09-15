from __future__ import annotations

from collections.abc import AsyncIterator

from parlio_voice.speech import speakable, speakable_stream


def test_summary_bullets_become_separate_sentences() -> None:
    text = (
        "Thank you for that, Keith. To confirm, I have:\n\n"
        "- Your name: Keith Wilson\n"
        "- Callback number: 07930, 934, 098\n"
        "- Address: 1 High Street, Manchester, M21 2DF\n"
        "- Urgency: Normal\n\n"
        "Is everything correct?"
    )
    out = speakable(text)
    assert "Your name is Keith Wilson." in out
    assert "Callback number is 0 7 9 3 0, 9 3 4, 0 9 8." in out
    assert "M 2 1, 2 D F" in out
    assert "Urgency is Normal." in out
    assert out.endswith("Is everything correct?")
    assert "-" not in out and "\n" not in out


def test_phone_numbers_spelt_out_in_prose() -> None:
    assert speakable("Calling 07881 311506.") == "Calling 0 7 8 8 1, 3 1 1, 5 0 6."
    assert "0 2 0, 4 6 2 0, 6 8 2 3" in speakable("Ring us on +44 20 4620 6823 any time")
    # short numbers / prices / years untouched
    assert speakable("It costs 120 pounds and we opened in 1998.") == (
        "It costs 120 pounds and we opened in 1998."
    )


def test_postcode_and_markdown() -> None:
    assert speakable("**Postcode:** SW1A 1AA") == "Postcode is S W 1 A, 1 A A."
    assert speakable("Your address is 12 Elm Road, M20 3PQ") == (
        "Your address is 12 Elm Road, M 2 0, 3 P Q."
    )


async def test_stream_flushes_at_sentence_boundaries() -> None:
    async def chunks() -> AsyncIterator[str]:
        for c in ["Your number is 079", "30 934 098. ", "Is that ", "right?"]:
            yield c

    out = "".join([s async for s in speakable_stream(chunks())])
    assert "0 7 9 3 0, 9 3 4, 0 9 8" in out
    assert out.strip().endswith("Is that right?")
