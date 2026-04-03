# Data Flow Diagram

```mermaid
flowchart LR
    GH["GitHub PR / diff"] --> ING["Ingress"]
    ING --> RAW["Raw PR snapshot"]
    RAW --> ANA["Diff analysis result"]
    ANA --> RET["Retrieval request"]
    IDX["Doc index"] --> RET
    RET --> CTX["Curated context"]
    CTX --> LLM["LLM structured output"]
    LLM --> PATCH["Doc patch"]
    PATCH --> VAL["Validation result"]
    VAL --> PUB["Publish to GitHub"]

    RAW -. "correlation id, stage logs" .-> LOG["Logs"]
    ANA -. "scores, file counts" .-> MET["Metrics"]
    RET -. "retrieval evidence" .-> LOG
    LLM -. "prompt summary, tokens, confidence" .-> LOG
    VAL -. "quality gates, failures" .-> MET
    PUB -. "final outcome" .-> MET
```

Что хранится:

- в `State Store`: session state, краткие артефакты retrieval, structured output, validation status;
- в индексе: чанки Markdown и metadata;
- в логах: сокращённый контекст и служебные идентификаторы;
- в метриках: latency, error class, confidence, fallback rate, acceptance signals.
