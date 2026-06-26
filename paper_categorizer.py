#!/usr/bin/env python3
"""
paper_categorizer.py

Reads papers/seen_papers_*.tsv and papers/new_papers_*.tsv.
For every row where 'category' is empty and both 'title' and 'abstract'
are non-empty, assigns one of:

  memory · mechinterp · architecture · vision · voice ·
  theory · training · performance · LLM

Classification is keyword-scoring over title (3×) and abstract (1×).
arXiv category codes in the 'keywords' field provide bonus signals.
The highest-scoring category wins; ties break by the priority order above.

Usage:
    python paper_categorizer.py                    # classify all files
    python paper_categorizer.py --dry-run          # print only, no writes
    python paper_categorizer.py --file FILE.tsv    # one specific file
    python paper_categorizer.py --stats            # category distribution
    python paper_categorizer.py --reclassify       # re-do already-set categories
"""

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

ROOT       = Path(__file__).parent
PAPERS_DIR = ROOT / "papers"

# Priority order: ties broken by earliest position in this list.
CATEGORIES = [
    "mechinterp", "voice", "vision", "memory",
    "theory", "architecture", "training", "performance", "LLM",
]

# ── Keyword rules ──────────────────────────────────────────────────────────────
# Each entry: (list_of_lowercase_phrases, score)
#   score 4 → specific multi-word phrase or proper-noun clearly in this category
#   score 2 → fairly specific single term, occasionally cross-category
# Title match = 3 × score; abstract match = 1 × score.
RULES: dict[str, list[tuple[list[str], int]]] = {

    "mechinterp": [
        (["mechanistic interpretability", "mechanistic analysis",
          "induction head", "superposition hypothesis",
          "polysemanticity", "causal tracing", "activation patching",
          "representation engineering", "linear representation hypothesis",
          "circuit analysis", "circuit discovery", "circuit in transformers",
          "sparse autoencoder", "sparse dictionary learning",
          "feature visualization", "causal intervention",
          "information flow analysis", "probing classifier",
          "grokking", "sycophancy analysis",
          "knowledge circuit", "mech interp"], 4),
        (["interpretability", "probing", "mechanistic", "induction circuit",
          "attention head analysis", "feature geometry",
          "internal representation", "knowledge editing"], 2),
    ],

    "voice": [
        (["speech recognition", "automatic speech recognition",
          "text-to-speech", "speech synthesis", "tts model",
          "voice conversion", "voice cloning",
          "speaker recognition", "speaker verification",
          "spoken language", "spoken dialogue",
          "speech-to-text", "speech foundation model",
          "audio language model", "speech language model",
          "audio generation", "music generation", "music synthesis",
          "speech enhancement", "noise cancellation",
          "acoustic model", "end-to-end speech",
          "mel spectrogram", "mel-spectrogram",
          "prosody", "phoneme", "waveform generation",
          "vocoder", "neural vocoder",
          "whisper model", "wav2vec", "hubert model", "conformer model",
          "audio codec", "sound generation", "audio diffusion",
          "asr system", "tts system"], 4),
        (["speech", "audio", "voice", "spoken", "acoustic",
          "phonetic", "speaker", "waveform"], 2),
    ],

    "vision": [
        (["image classification", "object detection", "semantic segmentation",
          "instance segmentation", "panoptic segmentation",
          "image generation", "image synthesis", "text-to-image",
          "stable diffusion", "dall-e", "imagen model",
          "visual question answering", "image captioning",
          "depth estimation", "optical flow",
          "3d reconstruction", "nerf", "gaussian splatting",
          "point cloud", "video understanding", "video generation",
          "action recognition", "pose estimation",
          "optical character recognition", "ocr model",
          "scene understanding", "visual grounding",
          "vision-language model", "vision language model",
          "visual instruction", "visual encoder",
          "vision transformer", "convolutional neural network",
          "video frame prediction", "image model", "diffusion model",
          "yolo", "detr", "segment anything", "sam model",
          "clip model", "vqa task", "multimodal model",
          "multimodal understanding", "image encoder"], 4),
        (["image", "visual", "vision", "video", "pixel",
          "frame", "scene", "rendering", "3d", "multimodal"], 2),
    ],

    "memory": [
        (["retrieval-augmented generation", "retrieval augmented generation",
          "rag system", "rag model", "rag pipeline",
          "memory-augmented", "memory augmented network",
          "external memory", "episodic memory",
          "memory bank", "memory module", "memory network",
          "associative memory", "hopfield network",
          "key-value memory", "compressive memory",
          "memory system", "neural memory",
          "long-term memory", "short-term memory",
          "contextual memory", "working memory", "memory capacity",
          "titans memory", "infinite context",
          "long-context retrieval", "in-context retrieval",
          "memory-enhanced", "recurrent memory transformer",
          "grounded retrieval", "external knowledge retrieval",
          "memory management for", "persistent memory"], 4),
        (["retrieval augmented", "augmented retrieval",
          "external knowledge", "memory slot", "memory cell",
          "knowledge store", "memory mechanism",
          "episodic buffer", "memory replay",
          "long-context memory"], 2),
    ],

    "theory": [
        (["pac learning", "vc dimension", "rademacher complexity",
          "generalization bound", "excess risk bound", "sample complexity",
          "convergence proof", "convergence rate analysis",
          "neural tangent kernel", "mean field theory",
          "information bottleneck", "statistical learning theory",
          "overparameterization", "double descent",
          "benign overfitting", "implicit regularization", "implicit bias",
          "loss landscape", "saddle point", "gradient flow analysis",
          "regret bound", "online learning theory",
          "learnability", "expressivity of",
          "approximation theory", "universal approximation",
          "computational complexity of learning",
          "we prove that", "we establish that",
          "tight bound", "minimax optimal"], 4),
        (["convergence analysis", "convergence rate", "convergence proof",
          "regret bound", "regret analysis",
          "learnability", "lower bound on", "upper bound on",
          "theoretical analysis", "approximation bound"], 2),
    ],

    "architecture": [
        (["state space model", "selective state space", "structured state space",
          "mamba model", "mamba architecture", "mamba2", "mamba 2",
          "flash attention", "ring attention",
          "linear attention", "local attention", "global-local attention",
          "mixture of experts", "mixture-of-experts", "moe layer",
          "grouped query attention", "multi-query attention",
          "sliding window attention",
          "rotary position embedding", "rope embedding", "alibi",
          "new activation function", "activation function design",
          "silu activation", "swish activation",
          "rms norm", "layer normalization design",
          "new tokenizer", "tokenization method", "bpe tokeniz",
          "byte pair encoding", "sentencepiece",
          "new architecture", "novel architecture",
          "rwkv model", "retnet model", "hyena model",
          "multi-head latent attention",
          "feed-forward network design", "mlp design",
          "parallel attention", "recurrent neural network design",
          "s4 model", "ssm layer", "hippo matrix",
          "positional encoding design", "relative positional",
          "attention mechanism design", "transformer design",
          "architecture search"], 4),
        (["transformer", "attention mechanism", "self-attention",
          "architecture", "ssm", "residual", "feed-forward",
          "positional encoding", "normalization", "tokenizer",
          "gating mechanism", "recurrent layer", "moe"], 2),
    ],

    "training": [
        (["lora", "low-rank adaptation", "qlora", "dora",
          "parameter-efficient fine-tuning", "peft method",
          "prefix tuning", "prompt tuning",
          "rlhf", "reinforcement learning from human feedback",
          "direct preference optimization", "dpo",
          "kahneman-tversky optimization", "grpo",
          "reward model training", "reward shaping",
          "continual learning", "catastrophic forgetting",
          "instruction tuning", "instruction fine-tuning",
          "supervised fine-tuning", "sft",
          "adapter tuning", "adapter layer",
          "optimizer design", "new optimizer",
          "adamw optimizer", "adafactor", "adan",
          "lion optimizer", "sophia optimizer",
          "learning rate schedule", "cosine annealing",
          "warmup schedule", "gradient clipping",
          "mixed precision training", "bfloat16 training",
          "curriculum learning", "data curriculum",
          "federated learning", "federated fine-tuning",
          "contrastive learning", "self-supervised pretraining",
          "masked language modeling", "masked image modeling",
          "post-training alignment", "alignment fine-tuning",
          "reinforcement learning for", "reinforcement learning to",
          "rl training", "rl fine-tuning",
          "proximal policy optimization for llm"], 4),
        (["fine-tuning", "finetuning", "training method",
          "gradient descent", "optimization algorithm",
          "weight update", "backpropagation", "pretraining method",
          "data augmentation", "regularization", "dropout",
          "reinforcement learning"], 2),
    ],

    "performance": [
        (["quantization", "post-training quantization",
          "quantization-aware training", "int8 ", "int4 ",
          " fp8 ", "4-bit quantiz", "8-bit quantiz",
          "knowledge distillation", "model compression",
          "network pruning", "weight pruning",
          "structured pruning", "unstructured pruning",
          "speculative decoding", "speculative sampling",
          "draft model", "early exit strategy",
          "adaptive computation", "layer skipping",
          "token dropping", "weight sharing",
          "model acceleration", "inference optimization",
          "efficient inference", "fast inference",
          "throughput optimization", "latency reduction",
          "flops reduction", "parameter reduction",
          "hardware-aware training", "hardware efficient",
          "sparse model", "sparse inference",
          "kv cache compression", "kv cache optimization",
          "paged attention", "flash decoding",
          "tensor parallelism", "pipeline parallelism",
          "model parallelism", "memory bandwidth optimization",
          "memory-efficient training",
          "compute-efficient", "energy efficient"], 4),
        (["compression", "efficient model", "lightweight model",
          "compact model", "faster inference", "fewer parameters",
          "smaller model", "prune", "quantize",
          "distillation", "efficient attention", "sparsity"], 2),
    ],

    "LLM": [
        (["large language model", "autoregressive language model",
          "in-context learning", "few-shot prompting", "zero-shot prompting",
          "chain-of-thought", "chain of thought", "cot prompting",
          "mathematical reasoning", "arithmetic reasoning",
          "commonsense reasoning", "logical reasoning",
          "hallucination in llm", "model hallucination",
          "emergent abilit", "emergent capabilities",
          "scaling law", "chinchilla scaling",
          "llama model", "llama 2", "llama 3", "llama-", "mistral model",
          "gemini model", "gpt-4", "gpt-3", "gpt4", "gpt3", "chatgpt",
          "claude model", "palm model", "palm2",
          "bloom model", "falcon model", "qwen model", "deepseek model",
          "phi model", "phi-", "gemma model", "codellama",
          "code generation model", "code synthesis",
          "program synthesis", "program generation",
          "language generation", "natural language generation",
          "instruction following", "instruction-following",
          "question answering model", "reading comprehension model",
          "language model pretraining", "causal language model",
          "next-token prediction", "pretraining language model",
          "llm training", "llm fine-tuning",
          "base language model", "foundation language model",
          "language model benchmark", "language understanding benchmark",
          # Agentic / tool use
          "llm agent", "language model agent", "agentic ai",
          "autonomous agent", "computer-use agent", "computer use agent",
          "web agent", "coding agent", "tool-use agent",
          "tool use in llm", "tool calling", "function calling",
          "multi-agent system", "multi-agent framework",
          "agent framework", "agent benchmark", "agent evaluation",
          "agentic framework", "agentic system",
          "skill-mediated", "task-solving agent",
          # Reasoning / prompting
          "step-by-step reasoning", "scratchpad", "self-consistency",
          "tree of thought", "least-to-most", "decomposition prompting",
          "process reward model", "outcome reward model",
          # Alignment / safety
          "red-teaming", "red teaming", "jailbreak",
          "safety alignment", "value alignment", "harmful content",
          "bias in language", "fairness in llm",
          # Multilinguality
          "multilingual model", "cross-lingual",
          "machine translation model"], 4),
        (["language model", "llm", "gpt", "generation",
          "reasoning", "pretraining", "instruction",
          "foundation model", "base model", "prompt",
          "agent", "agentic", "tool use", "benchmark",
          "evaluation of", "dialog", "dialogue system"], 2),
    ],
}

# arXiv category codes → bonus points per CATEGORIES entry
ARXIV_BONUS: dict[str, dict[str, int]] = {
    "cs.cv":  {"vision": 6},
    "eess.iv": {"vision": 4},
    "eess.as": {"voice": 6},
    "eess.sp": {"voice": 4},
    "cs.cl":  {"LLM": 4},
    "cs.ne":  {"architecture": 3},
    "stat.ml": {},  # too broad for theory bonus; most ML papers use this category
    "cs.lg":  {},   # too broad to assign bonus
    "cs.ai":  {},
}


# ── Scoring ────────────────────────────────────────────────────────────────────

def _match(phrase: str, text: str) -> bool:
    """
    Return True if phrase appears in text.
    Single-word phrases (no spaces, ≤10 chars) use word-boundary matching so that
    "lora" matches "lora:" but not "explora"; "ssm" matches "ssm" but not "prism".
    Multi-word phrases use plain substring matching.
    """
    if " " not in phrase and len(phrase) <= 10:
        return bool(re.search(r"\b" + re.escape(phrase) + r"\b", text))
    return phrase in text


def _phrase_score(text: str, rules: list[tuple[list[str], int]]) -> dict[str, int]:
    """Return per-category raw scores for one text field."""
    scores: dict[str, int] = {c: 0 for c in CATEGORIES}
    text_lc = text.lower()
    for cat, rule_groups in RULES.items():
        for phrases, weight in rule_groups:
            for phrase in phrases:
                if _match(phrase, text_lc):
                    scores[cat] += weight
    return scores


MIN_SCORE = 2  # minimum total score to assign a specific category; else "LLM"

def classify(title: str, abstract: str, keywords: str = "") -> str:
    """
    Return the best-matching category for a paper.
    Title carries 3× the weight of abstract text.
    Falls back to 'LLM' when no category reaches MIN_SCORE.
    """
    t_scores = _phrase_score(title, RULES)
    a_scores = _phrase_score(abstract, RULES)

    totals: dict[str, int] = {
        c: 3 * t_scores[c] + a_scores[c] for c in CATEGORIES
    }

    # Bonus from arXiv category codes in the keywords field
    for code in re.split(r"[,\s]+", keywords.lower()):
        bonus_map = ARXIV_BONUS.get(code.strip(), {})
        for cat, pts in bonus_map.items():
            totals[cat] += pts

    best_score = max(totals.values())
    if best_score < MIN_SCORE:
        return "LLM"   # all AI/ML papers belong here when nothing more specific matches

    # Highest score, tie-broken by CATEGORIES priority order
    return max(CATEGORIES, key=lambda c: (totals[c], -CATEGORIES.index(c)))


# ── File processing ────────────────────────────────────────────────────────────

def process_file(path: Path, dry_run: bool, reclassify: bool) -> tuple[int, int]:
    """
    Classify uncategorized papers in one TSV file.
    Returns (papers_found, papers_classified).
    Adds a 'category' column if the file doesn't have one.
    """
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if not rows:
        return 0, 0

    # Add 'category' column if missing
    if "category" not in fieldnames:
        fieldnames.append("category")
        for row in rows:
            row.setdefault("category", "")

    # Determine which rows need classification
    if reclassify:
        candidates = [
            (i, row) for i, row in enumerate(rows)
            if row.get("title") and row.get("abstract")
        ]
    else:
        candidates = [
            (i, row) for i, row in enumerate(rows)
            if not row.get("category")
            and row.get("title")
            and row.get("abstract")
        ]

    if not candidates:
        return 0, 0

    print(f"  {path.name}: {len(candidates)} paper(s) to classify")

    classified = 0
    for i, row in candidates:
        cat = classify(
            row.get("title", ""),
            row.get("abstract", ""),
            row.get("keywords", ""),
        )
        rows[i]["category"] = cat
        classified += 1
        if dry_run:
            title = row.get("title", "")[:70]
            print(f"    [{cat:<12}] {title}")

    if not dry_run:
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(rows)

    return len(candidates), classified


def show_stats(paths: list[Path]) -> None:
    """Print distribution of categories across all files."""
    counter: Counter = Counter()
    total = 0
    for path in paths:
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            if "category" not in (reader.fieldnames or []):
                continue
            for row in reader:
                cat = row.get("category", "").strip()
                if cat:
                    counter[cat] += 1
                    total += 1
    if not counter:
        print("No categorized papers found.")
        return
    print(f"\nCategory distribution ({total} categorized papers):")
    for cat, count in sorted(counter.items(), key=lambda x: -x[1]):
        bar = "█" * (count * 30 // max(counter.values()))
        print(f"  {cat:<14} {count:5d}  {bar}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify AI/ML papers by topic using keyword scoring."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print categories without writing to files")
    parser.add_argument("--file", metavar="FILE",
                        help="Process only this TSV file")
    parser.add_argument("--stats", action="store_true",
                        help="Show category distribution and exit")
    parser.add_argument("--reclassify", action="store_true",
                        help="Re-classify papers that already have a category")
    args = parser.parse_args()

    if args.file:
        paths = [Path(args.file)]
    else:
        paths = (sorted(PAPERS_DIR.glob("seen_papers_*.tsv"))
                 + sorted(PAPERS_DIR.glob("new_papers_*.tsv")))

    if args.stats:
        show_stats(paths)
        return

    if not paths:
        print("No TSV files found.", file=sys.stderr)
        sys.exit(1)

    total_found = total_classified = 0
    for path in paths:
        if not path.exists():
            print(f"  [!] {path}: not found", file=sys.stderr)
            continue
        found, classified = process_file(path, args.dry_run, args.reclassify)
        total_found      += found
        total_classified += classified

    if total_found == 0:
        print("Nothing to classify — all eligible papers already have a category.")
    else:
        action = "Would classify" if args.dry_run else "Classified"
        print(f"\n{action} {total_classified}/{total_found} papers.")


if __name__ == "__main__":
    main()
