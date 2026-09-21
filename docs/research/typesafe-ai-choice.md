# TypeSafe AI `Choice` primitive

> Researched from TypeSafe AI first-party documentation only. Accessed 2026-09-20 (Asia/Hong_Kong). The API examples below are documentation examples; no authenticated live request was made. Performance and calibration statements are therefore vendor claims, not independently reproduced results.

## Bottom line

`Choice` is TypeSafe AI's closed-set, single-answer classification primitive. A caller supplies some `state`, a focused question, and named candidate options. The System One model returns:

- `choice`: the option with the highest probability;
- `probabilities`: a value for every supplied option; and
- `confidence`: a separate 0-1 statistic derived from how concentrated that probability distribution is.

It is intended for judgments such as ticket routing, document categorization, or programming-language detection. It is not the right primitive for a position on an ordered spectrum (`Score`) or a yes/no proposition (`Noul`). [Choice](https://docs.typesafe.ai/primitives/choice) · [Primitives](https://docs.typesafe.ai/primitives)

The important architectural point is that `Choice` is not a model of its own. It is one of three typed question/answer shapes exposed by TypeSafe's System One API. TypeSafe currently describes Jev as its flagship and first System One model: it evaluates focused questions against state and returns structured decisions rather than generated prose. [Introduction](https://docs.typesafe.ai/introduction) · [System One](https://docs.typesafe.ai/concepts/system-one)

## Where it sits in TypeSafe AI

| Primitive | Intended judgment | Main result |
|---|---|---|
| `Choice` | Which one of these unordered options? | selected option, distribution, confidence |
| `Score` | Where on this described ordered scale? | probability-weighted score, distribution, confidence |
| `Noul` | Is this proposition true? | probability of yes/true |

All three can be mixed in one request. Every question sees the same `state`, is evaluated independently, and returns under the caller-selected question ID. TypeSafe recommends keeping each question atomic and combining answers with deterministic application logic. [Primitives](https://docs.typesafe.ai/primitives) · [How to build with TypeSafe](https://docs.typesafe.ai/concepts/how-to-build-with-system-one)

The current model documentation says Jev accepts text only, represented as a string, JSON object, or array of text values; images, audio, and video are not supported directly. It also says English is the primary training language and recommends workload-specific testing for other languages. [System One](https://docs.typesafe.ai/concepts/system-one) · [Models](https://docs.typesafe.ai/models)

## HTTP contract

The endpoint is:

```http
POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

The top-level request fields are `state`, `model`, and `questions`. Each Choice question uses `type: "choice"`, `instructions`, and a `criteria` map whose keys are the answer labels. The question ID (for example, `department`) is for the caller's code and is not sent to the underlying model; the answer is returned under that same ID. [HTTP API](https://docs.typesafe.ai/api) · [Choice request structure](https://docs.typesafe.ai/primitives/choice#request-structure)

```json
{
  "state": "My running shoes arrived in the wrong size. Can I swap them?",
  "model": "jev-latest",
  "questions": {
    "department": {
      "type": "choice",
      "instructions": "Which team should handle this?",
      "criteria": {
        "returns": "Exchanges, wrong or damaged items",
        "shipping": "Delivery status, delays, lost packages",
        "billing": "Charges, invoices, payment problems"
      }
    }
  }
}
```

The corresponding answer shape is:

```json
{
  "model": "jev-1.13.0",
  "answers": {
    "department": {
      "type": "choice",
      "choice": "returns",
      "confidence": 1.0,
      "probabilities": {
        "shipping": 0.0,
        "returns": 1.0,
        "billing": 0.0
      }
    }
  },
  "usage": {
    "input_tokens": 328,
    "output_tokens": 34
  }
}
```

The API defines `choice` as the highest-probability option, `probabilities` as all supplied options mapped to probability values, and `confidence` as a value derived from that distribution. [Choice response structure](https://docs.typesafe.ai/primitives/choice#response-structure) · [HTTP Choice answer](https://docs.typesafe.ai/api#choice-answer)

## Semantics that matter in application code

1. **One winner, not multi-select.** `choice` is always one supplied label. When more than one label plausibly applies, the distribution can remain split. In TypeSafe's ambiguous support-ticket example, `returns` wins at `0.61`, `billing` has `0.35`, and confidence is `0.42`; the sample code assigns the winner and separately notifies a second team when its probability exceeds an application threshold. This is how a caller can preserve secondary hypotheses without treating `Choice` itself as a multi-label response. [Complex Choice example](https://docs.typesafe.ai/primitives/choice#a-more-complex-example)

2. **Confidence is not the winning probability.** It summarizes how peaked or flat the complete distribution is. The documentation advises using it to gate behavior—act, ask for confirmation, or escalate—while choosing thresholds according to the cost of error. It also exposes the complete distribution so callers may compute a different uncertainty measure. [Confidence](https://docs.typesafe.ai/confidence)

3. **Options are caller-defined and constrained.** A Choice accepts at most 255 options. TypeSafe recommends sending the full applicable list rather than a shortlist and adding `other` or `none of the above` when the set may not be exhaustive. [Choice good practice](https://docs.typesafe.ai/primitives/choice#good-practice-ask-more-than-one-question-per-call) · [HTTP Choice schema](https://docs.typesafe.ai/api#choice)

4. **Descriptions can carry structure.** `instructions` can be a string, object, or array. Each `criteria` value can be a string, object, array, or `null`. Structured objects can distinguish close options with fields such as what an option covers, what it excludes, and examples; those field names are caller-chosen rather than reserved API keys. [Structured instructions and criteria](https://docs.typesafe.ai/primitives/choice#structured-instructions-and-criteria)

5. **Batch independent questions against one state.** TypeSafe recommends placing every potentially useful independent question in one call, even if application code later ignores some answers. The documented semantics are parallel and isolated evaluation; extra questions still consume tokens. A true dependency—where an earlier answer determines later state or options—requires another request. [Ask multiple questions together](https://docs.typesafe.ai/primitives#ask-multiple-questions-together)

6. **Large hierarchies are composed, not flattened blindly.** The Choice page recommends chaining levels of a taxonomy, and its official hierarchical-classification cookbook uses beam search over Choice probabilities to retain several candidate paths rather than greedily committing at every level. [Hierarchical classification](https://docs.typesafe.ai/cookbooks/hierarchical_classification)

## SDK surface

The Python SDK exposes `Choice` and synchronous/asynchronous clients. A typical call is `TypeSafeClient.system_one(state=..., questions={"department": Choice(...)})`; the result is available from `response.answers["department"]`, and the Python response type also exposes a filtered `response.choices` mapping. [Python SDK](https://docs.typesafe.ai/sdk/python) · [Python Choice type](https://docs.typesafe.ai/sdk/python/api/types/questions#typesafe_sdk.Choice) · [Python response type](https://docs.typesafe.ai/sdk/python/api/types/responses#typesafe_sdk.SystemOneResponse)

The JavaScript/TypeScript SDK uses `choice(instructions, criteria)` and `client.systemOne(...)`. Its generic `ChoiceQuestion<T>`/`ChoiceResponse<T>` types retain the criteria keys so the selected label is typed as `keyof T & string`. [JavaScript SDK](https://docs.typesafe.ai/sdk/javascript) · [`choice()`](https://docs.typesafe.ai/sdk/javascript/api/functions/choice) · [`ChoiceResponse<T>`](https://docs.typesafe.ai/sdk/javascript/api/interfaces/ChoiceResponse)

The SDKs are conveniences around the same HTTP API and provide typed questions/answers plus default retry handling; direct HTTP calls remain supported. [Client SDKs](https://docs.typesafe.ai/sdk)

## Version and documentation caveats

- **Model alias is mutable.** On the access date, `jev-latest` and `jev-preview` both resolve to `jev-1.13.0`. The docs explicitly warn that aliases move when a release ships and that answers may change. Applications that tune thresholds against a particular model should pin the version and log the response's versioned `model` field. [Models and aliases](https://docs.typesafe.ai/models#aliases)
- **The exact production confidence formula is not documented as an API contract.** The confidence page says it is derived from the distribution. Its interactive three-option demo uses `(3 × largest probability − 1) / 2` only as an approximation for that demo, so that expression should not be reimplemented as if it were the service formula. [Confidence](https://docs.typesafe.ai/confidence)
- **Probability totals should be handled with floating-point tolerance.** The primitive and HTTP pages say the values sum to 1, while the Python SDK reference says they sum to approximately 1. [Choice response structure](https://docs.typesafe.ai/primitives/choice#response-structure) · [Python `ChoiceAnswer`](https://docs.typesafe.ai/sdk/python/api/types/responses#typesafe_sdk.ChoiceAnswer)
- **`instructions` has a documentation mismatch.** The HTTP API reference marks it required, but the current Python `Choice` schema gives it a `None` default and the JavaScript `ChoiceQuestion` interface marks it optional. For portable behavior, supply explicit, non-null instructions. [HTTP Choice schema](https://docs.typesafe.ai/api#choice) · [Python `Choice`](https://docs.typesafe.ai/sdk/python/api/types/questions#typesafe_sdk.Choice) · [JavaScript `ChoiceQuestion<T>`](https://docs.typesafe.ai/sdk/javascript/api/interfaces/ChoiceQuestion)
- **Calibration is a population-level property, not a guarantee for one answer.** TypeSafe says its System One models are trained for calibrated decisions, but also says calibration is measured over groups of predictions and does not guarantee an individual result. Confidence thresholds therefore need validation on the target workload. [System One](https://docs.typesafe.ai/concepts/system-one#how-it-differs-from-an-llm)
- **Parallel-performance claims were not independently verified here.** The docs say adding independent questions barely changes response time, but this note establishes only the documented API behavior, not workload-specific latency or cost. [Primitives](https://docs.typesafe.ai/primitives#ask-multiple-questions-together)

## Official sources consulted

- https://docs.typesafe.ai/primitives/choice
- https://docs.typesafe.ai/primitives
- https://docs.typesafe.ai/api
- https://docs.typesafe.ai/introduction
- https://docs.typesafe.ai/concepts/system-one
- https://docs.typesafe.ai/concepts/how-to-build-with-system-one
- https://docs.typesafe.ai/confidence
- https://docs.typesafe.ai/models
- https://docs.typesafe.ai/sdk
- https://docs.typesafe.ai/sdk/python
- https://docs.typesafe.ai/sdk/python/api/types/questions
- https://docs.typesafe.ai/sdk/python/api/types/responses
- https://docs.typesafe.ai/sdk/javascript
- https://docs.typesafe.ai/sdk/javascript/api/functions/choice
- https://docs.typesafe.ai/sdk/javascript/api/interfaces/ChoiceQuestion
- https://docs.typesafe.ai/sdk/javascript/api/interfaces/ChoiceResponse
- https://docs.typesafe.ai/cookbooks/hierarchical_classification
