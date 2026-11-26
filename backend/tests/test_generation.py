"""Pipeline contract tests with scripted model responses."""

import json
from collections.abc import Callable

import pytest

from app.core.config import settings
from app.generation import (
    ExamQuestion,
    GenerationError,
    _exam_batches,
    _solve_exam,
    classify_document,
    generate_exam,
    generate_notes,
)
from app.llm import LLMError


class ScriptedClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.prompts = []

    async def complete_json[T](
        self, system: str, prompt: str, *, validate: Callable[[object], T],
    ) -> tuple[T, str]:
        self.prompts.append((system, prompt))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return validate(response), "fixture-model"


NOTES = ["Photosynthesis converts sunlight into chemical energy. Chlorophyll absorbs "
         "light in plant leaves, and the process releases oxygen into the atmosphere."]


async def test_notes_validates_pages_options_and_duplicates():
    valid = {
        "question": "What does chlorophyll absorb in leaves?",
        "options": ["Light", "Water", "Oxygen", "Glucose"],
        "correct_index": 0,
        "explanation": "Chlorophyll absorbs light for photosynthesis.",
        "source_page": 1,
        "evidence": "Chlorophyll absorbs light in plant leaves",
    }
    client = ScriptedClient([{"questions": [
        valid, valid,
        {**valid, "question": "Wrong cited page question", "source_page": 3},
        {**valid, "question": "Duplicate answer choices question",
         "options": ["Light", "Light", "Oxygen", "Glucose"]},
        {**valid, "question": "Who first landed on the Moon?", "evidence": "........"},
    ]}, {"questions": [{**valid, "question": "Which pigment captures solar energy?"}]}])
    quiz = await generate_notes(NOTES, 2, "medium", client)
    assert len(quiz.questions) == 2
    assert "Create 1 medium" in client.prompts[1][1]
    assert valid["question"] in client.prompts[1][1]
    assert quiz.questions[0]["source_page"] == 1
    assert quiz.questions[0]["options"][quiz.questions[0]["correct_index"]] == "Light"
    assert quiz.questions[0]["answer_source"] == "generated"
    assert "[Page 1]" in client.prompts[0][1]
    short = ScriptedClient([{"questions": [valid]}] * 3)
    partial = await generate_notes(NOTES, 5, "medium", short)
    assert len(partial.questions) == 1


async def test_notes_carries_shortfall_to_next_chunk(monkeypatch):
    monkeypatch.setattr(settings, "llm_chunk_tokens", 300)
    pages = [NOTES[0] * 5, NOTES[0] * 5]
    valid = {
        "question": "What does chlorophyll absorb in leaves?",
        "options": ["Light", "Water", "Oxygen", "Glucose"],
        "correct_index": 0,
        "explanation": "Chlorophyll absorbs light for photosynthesis.",
        "source_page": 2,
        "evidence": "Chlorophyll absorbs light in plant leaves",
    }
    client = ScriptedClient([{"questions": []}] * 3 + [{"questions": [
        valid, {**valid, "question": "Which pigment captures solar energy?"},
    ]}])
    quiz = await generate_notes(pages, 2, "medium", client)
    assert len(quiz.questions) == 2
    assert all(q["source_page"] == 2 for q in quiz.questions)
    assert "Create 2 medium" in client.prompts[-1][1]


async def test_notes_skips_chunk_with_invalid_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_chunk_tokens", 300)
    pages = [NOTES[0] * 5] * 3
    valid = {
        "question": "What does chlorophyll absorb in leaves?",
        "options": ["Light", "Water", "Oxygen", "Glucose"],
        "correct_index": 0,
        "explanation": "Chlorophyll absorbs light for photosynthesis.",
        "source_page": 1,
        "evidence": "Chlorophyll absorbs light in plant leaves",
    }
    third_page = {**valid, "source_page": 3}
    client = ScriptedClient([
        {"questions": [valid]},
        {"unexpected": []},
        {"questions": [
            {**third_page, "question": "Which pigment captures solar energy?"},
            {**third_page, "question": "What absorbs light to drive photosynthesis?"},
        ]},
    ])
    quiz = await generate_notes(pages, 3, "medium", client)
    assert "Create 2 medium" in client.prompts[-1][1]
    assert [question["source_page"] for question in quiz.questions] == [1, 3, 3]


async def test_notes_fails_when_model_produces_no_valid_questions():
    client = ScriptedClient([{"questions": [{"question": "bad"}]}] * 3)
    with pytest.raises(GenerationError, match="no valid questions"):
        await generate_notes(NOTES, 5, "easy", client)


async def test_notes_evidence_requires_whole_phrase_in_current_excerpt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "llm_chunk_tokens", 300)
    pages = ["Inactive proteins were stored. " + "Reference only. " * 76
             + "Plant cells produce oxygen during photosynthesis."]
    valid = {
        "question": "What gas do plant cells produce?",
        "options": ["Oxygen", "Nitrogen", "Methane", "Hydrogen"],
        "correct_index": 0,
        "explanation": "Plant cells produce oxygen.",
        "source_page": 1,
        "evidence": "cells produce oxygen",
    }
    invalid = [
        {**valid, "question": "Which protein was active?", "evidence": "active protein"},
        valid,
    ]
    client = ScriptedClient([{"questions": invalid}] * 3 + [{"questions": [valid]}])
    quiz = await generate_notes(pages, 1, "medium", client)
    assert [question["text"] for question in quiz.questions] == [valid["question"]]
    assert all(valid["evidence"] not in prompt for _, prompt in client.prompts[:3])
    assert valid["evidence"] in client.prompts[-1][1]


@pytest.mark.parametrize("field", ["question", "explanation", "options"])
async def test_notes_rejects_model_text_postgresql_cannot_store(field: str) -> None:
    valid = {
        "question": "What does chlorophyll absorb?",
        "options": ["Light", "Water", "Oxygen", "Glucose"],
        "correct_index": 0,
        "explanation": "Chlorophyll absorbs light.",
        "source_page": 1,
        "evidence": "Chlorophyll absorbs light",
    }
    bad_value = (
        ["L\x00ight", "Water", "Oxygen", "Glucose"] if field == "options" else "invalid\x00text"
    )
    client = ScriptedClient([{"questions": [{**valid, field: bad_value}, valid]}])
    quiz = await generate_notes(NOTES, 1, "medium", client)
    assert quiz.questions[0]["explanation"] == valid["explanation"]
    assert quiz.questions[0]["text"] == valid["question"]
    assert set(quiz.questions[0]["options"]) == set(valid["options"])


async def test_classification_returns_type_and_confidence():
    client = ScriptedClient([{"doc_type": "exam", "confidence": 0.65}])
    assert await classify_document(NOTES, client) == ("exam", 0.65)


async def test_exam_preserves_wording_and_marks_key_disagreement():
    pages = ["1. Which planet is closest to the Sun?\nA. Mercury. B. Venus.",
             "C. Earth. D. Mars.\nAnswer key: 1. A.", "Appendix I", "Appendix II", "Appendix III"]
    client = ScriptedClient([
        {"questions": [{"number": "1", "question": "Which planet is closest to the Sun?",
                        "options": ["Venus", "Mercury", "Earth", "Mars"],
                        "source_page": 1, "question_type": "mcq"}]},
        {"answers": [{"number": "1", "correct_index": 1, "evidence": "1. A"}]},
        {"answers": [{"number": "1", "correct_index": 1}]},
    ])
    quiz = await generate_exam(pages, client)
    assert len(quiz.questions) == 1
    assert quiz.questions[0]["text"] == "Which planet is closest to the Sun?"
    assert quiz.questions[0]["correct_index"] == 0
    assert quiz.questions[0]["options"] == ["Mercury", "Venus", "Earth", "Mars"]
    assert "Answer key: 1. A" in client.prompts[1][1]
    assert quiz.questions[0]["answer_source"] == "answer_key"
    assert quiz.questions[0]["flagged"] is True


@pytest.mark.parametrize(
    ("question", "final_choice"),
    [
        ("Which option applies?", "None of the above"),
        ("Use only the given data. Which option applies?", "None"),
    ],
)
async def test_exam_rejects_partial_stem_or_final_option(question: str, final_choice: str) -> None:
    pages = [
        "1. Use only the given data.\nWhich option applies?\n"
        "A. Mercury\nB. Venus\nC. Earth\nD. None\nof the above",
    ]
    client = ScriptedClient([{"questions": [{
        "number": 1, "question": question,
        "options": ["Mercury", "Venus", "Earth", final_choice], "source_page": 1,
    }]}])
    with pytest.raises(GenerationError, match="No exam questions could be extracted"):
        await generate_exam(pages, client)


async def test_exam_keeps_repeated_stems_and_signed_choices_in_printed_order() -> None:
    stem = "Which number in the choices satisfies the equation?"
    first_options = ["-2", "2", "3", "4"]
    second_options = ["-5", "5", "6", "7"]
    pages = [
        f"1. {stem}\nA. -2 B. 2 C. 3 D. 4\n2. {stem}\nA. -5 B. 5 C. 6 D. 7\n"
        "Answer key: 1. A, 2. B",
    ]
    first = {"number": 2, "question": stem, "options": first_options[::-1], "source_page": 1}
    second = {**first, "number": 1, "options": second_options[::-1]}
    client = ScriptedClient([
        {"questions": [second, first, first]},
        {"answers": [{"number": 1, "evidence": "1. A"}, {"number": 2, "evidence": "2. B"}]},
        {"answers": [{"number": 1, "correct_index": 0}, {"number": 2, "correct_index": 1}]},
    ])
    quiz = await generate_exam(pages, client)
    assert [question["options"] for question in quiz.questions] == [first_options, second_options]
    assert [question["correct_index"] for question in quiz.questions] == [0, 1]
    assert all(question["answer_source"] == "answer_key" for question in quiz.questions)
    assert len({question["question_hash"] for question in quiz.questions}) == 2


async def test_exam_uses_printed_question_number_to_match_key():
    stem = "Which planet is closest to the Sun?"
    options = ["Mercury", "Venus", "Earth", "Mars"]
    pages = [
        f"7. {stem} {' '.join(options)}\n"
        "8. Which planet is the Red Planet? Mercury Venus Earth Mars\n"
        "Answer key: 7. A, 8. D",
    ]
    client = ScriptedClient([
        {"questions": [{"number": 8, "question": stem, "options": options, "source_page": 1}]},
        {"answers": [{"number": 7, "evidence": "7. A"}, {"number": 8, "evidence": "8. D"}]},
        {"answers": [{"number": 1, "correct_index": 3}]},
    ])
    quiz = await generate_exam(pages, client)
    question = quiz.questions[0]
    assert question["correct_index"] == 0
    assert question["answer_source"] == "answer_key"
    assert question["flagged"] is True


@pytest.mark.parametrize("failed_verification", [LLMError("provider unavailable"), {}])
async def test_exam_retains_keyed_answers_when_verification_fails(
    failed_verification: object,
) -> None:
    stem = "Which planet is closest to the Sun?"
    options = ["Mercury", "Venus", "Earth", "Mars"]
    pages = [f"1. {stem} {' '.join(options)}\nAnswer key: 1. A"]
    client = ScriptedClient([
        {"questions": [{"number": 1, "question": stem, "options": options, "source_page": 1}]},
        {"answers": [{"number": 1, "evidence": "1. A"}]},
        failed_verification,
    ])
    quiz = await generate_exam(pages, client)
    assert quiz.questions[0]["answer_source"] == "answer_key"
    assert quiz.questions[0]["correct_index"] == 0


@pytest.mark.parametrize("failed_excerpt", [LLMError("provider unavailable"), {}])
async def test_exam_retains_verified_key_when_later_excerpt_fails(
    monkeypatch: pytest.MonkeyPatch, failed_excerpt: object,
) -> None:
    monkeypatch.setattr(settings, "llm_chunk_tokens", 300)
    stem = "Which planet is closest to the Sun?"
    options = ["Mercury", "Venus", "Earth", "Mars"]
    pages = [f"1. {stem} {' '.join(options)}\nAnswer key: 1. A\n"
             + "Reference information. " * 70]
    client = ScriptedClient([
        {"questions": [{"number": 1, "question": stem, "options": options, "source_page": 1}]},
        {"answers": [{"number": 1, "evidence": "1. A"}]},
        failed_excerpt,
        {"answers": [{"number": 1, "correct_index": 0}]},
    ])
    quiz = await generate_exam(pages, client)
    assert quiz.questions[0]["answer_source"] == "answer_key"
    assert quiz.questions[0]["correct_index"] == 0


async def test_exam_without_key_marks_ai_solved_and_rejects_invented_wording():
    pages = ["1. Which planet is closest to the Sun? Mercury Venus Earth Mars. "
             "Circle the best answer to each multiple choice question."]
    client = ScriptedClient([
        {"questions": [
            {"number": "1", "question": "Which planet is closest to the Sun?",
             "options": ["Mercury", "Venus", "Earth", "Mars"], "source_page": 1},
            {"number": "2", "question": "What is the capital of Jupiter?",
             "options": ["A", "B", "C", "D"], "source_page": 1},
        ]},
        {"answers": [{"number": "1", "correct_index": 0, "evidence": "1. A"}]},
        {"answers": [{"number": "1", "correct_index": 0}]},
    ])
    quiz = await generate_exam(pages, client)
    assert len(quiz.questions) == 1
    assert quiz.questions[0]["answer_source"] == "ai_solved"


async def test_exam_solve_uses_passage_from_preceding_page() -> None:
    passage = "Read this passage before answering. Mara stored the sample in compartment Delta."
    stem = "Which compartment held the sample?"
    options = ["Alpha", "Beta", "Gamma", "Delta"]
    client = ScriptedClient([
        {"questions": [{"number": 1, "question": stem, "options": options, "source_page": 2}]},
        {"answers": []},
        {"answers": [{"number": 1, "correct_index": 3}]},
    ])
    quiz = await generate_exam([passage, f"1. {stem} {' '.join(options)}"], client)
    solver_input = json.loads(client.prompts[-1][1].split("\n", 1)[1])
    assert passage in solver_input[0]["context"]
    assert quiz.questions[0]["answer_source"] == "ai_solved"
    assert quiz.questions[0]["correct_index"] == 3


@pytest.mark.parametrize("key", ["Answer key: 1. D", "1. D"])
async def test_exam_key_remains_ambiguous_when_an_extraction_is_missing(key: str) -> None:
    stem = "Which planet is closest to the Sun?"
    options = ["Mercury", "Venus", "Earth", "Mars"]
    pages = [
        f"Section A\n1. {stem} {' '.join(options)}",
        "Section B\n1. Which planet is known as the Red Planet? Mercury Venus Earth Mars",
        key,
    ]
    client = ScriptedClient([
        {"questions": [{"number": 1, "question": stem, "options": options, "source_page": 1}]},
        {"answers": [{"number": 1, "evidence": "1. D"}]},
        {"answers": [{"number": 1, "correct_index": 0}]},
    ])
    quiz = await generate_exam(pages, client)
    assert quiz.questions[0]["answer_source"] == "ai_solved"
    assert quiz.questions[0]["correct_index"] == 0


@pytest.mark.parametrize(
    ("key_text", "labeled"),
    [
        ("Answer key\nSection A: 1. A\nSection B: 1. D", True),
        ("Answer key: 1. A", False),
        ("Answers: 1. A", False),
        ("1. A\n1. A", False),
    ],
)
async def test_final_section_keys_with_restarted_numbering(key_text, labeled):
    stems = ["Which planet is closest to the Sun?", "Which planet is known as the Red Planet?"]
    options = ["Mercury", "Venus", "Earth", "Mars"]
    pages = [
        f"Section {section}\n1. {stem} {' '.join(options)}"
        for section, stem in zip("AB", stems, strict=True)
    ]
    pages.append(key_text)
    answers = [{"number": 1, "evidence": "1. A"}]
    if labeled:
        answers.append({"number": 1, "evidence": "1. D"})
    client = ScriptedClient([
        {"questions": [
            {"number": 1, "question": stem, "options": options, "source_page": i}
            for i, stem in enumerate(stems, 1)
        ]},
        {"answers": answers},
        {"answers": [{"number": 1, "correct_index": 0}, {"number": 2, "correct_index": 3}]},
    ])
    quiz = await generate_exam(pages, client)
    source = "answer_key" if labeled else "ai_solved"
    assert [(q["correct_index"], q["answer_source"], q["flagged"]) for q in quiz.questions] == [
        (0, source, False), (3, source, False),
    ]


async def test_exam_solver_limits_request_size_and_combines_batches(monkeypatch):
    monkeypatch.setattr(settings, "llm_chunk_tokens", 300)
    questions = [
        ExamQuestion(
            number=1, question=f"Question {i}: " + "text " * 100,
            options=["A", "B", "C", "D"], source_page=1,
        )
        for i in range(5)
    ]

    class SizeLimitedClient:
        async def complete_json[T](
            self, system: str, prompt: str, *, validate: Callable[[object], T],
        ) -> tuple[T, str]:
            assert len(prompt.encode()) <= 1200
            batch = json.loads(prompt.split("\n", 1)[1])
            return validate({"answers": [
                {"number": q["number"], "correct_index": q["number"] % 4} for q in batch
            ]}), "fixture-model"

    contexts = ['The passage says "quoted text". ' * 60] * len(questions)
    solved = await _solve_exam(questions, SizeLimitedClient(), contexts, {})
    assert solved == {0: 1, 1: 2, 2: 3, 3: 0, 4: 1}


def test_exam_batch_keeps_question_across_page_break():
    batches = _exam_batches([
        "1. First question\nA. Option one",
        "B. Option two\n2. Second question\nA. Another option",
    ], 60)
    assert "B. Option two" in batches[0][0]
    assert batches[0][1] == {1, 2}
    assert "2. Second question" in batches[1][0]
