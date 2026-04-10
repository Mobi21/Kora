"""Local embedding model using sentence-transformers with nomic-embed-text-v1.5.

Generates 768-dimensional embeddings locally without any API calls.
The nomic-embed-text-v1.5 model supports task-type prefixes for improved
retrieval quality (search_document for storage, search_query for queries).

Device selection: MPS (Mac GPU) > CUDA (NVIDIA GPU) > CPU (fallback).
Model loading is lazy -- the model is downloaded and loaded on first use.

Thread safety: model.encode() is NOT thread-safe on MPS/CUDA, so all
encode calls are serialized through a threading.Lock.
"""

import math
import threading

import structlog
import torch

from kora_v2.core.settings import MemorySettings

logger = structlog.get_logger()

# Nomic task-type prefixes (required by the model for optimal performance)
TASK_PREFIX_MAP = {
    "search_document": "search_document: ",
    "search_query": "search_query: ",
    "clustering": "clustering: ",
    "classification": "classification: ",
}

# Map legacy Gemini task types to nomic prefixes
GEMINI_TO_NOMIC_TASK_MAP = {
    "RETRIEVAL_DOCUMENT": "search_document",
    "RETRIEVAL_QUERY": "search_query",
}

DEFAULT_MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5"
DEFAULT_DIMENSION = 768


def _l2_normalize(vector: list[float]) -> list[float]:
    """L2 normalize a vector to unit length.

    Required for consistent cosine similarity comparisons.
    Nomic embeddings benefit from explicit normalization after extraction.

    Args:
        vector: Raw embedding vector.

    Returns:
        Unit-length vector (or original if zero-norm).
    """
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return vector
    return [x / norm for x in vector]


def _detect_device(preferred: str = "auto") -> str:
    """Detect the best available compute device.

    Priority: MPS (Apple Silicon GPU) > CUDA (NVIDIA GPU) > CPU.

    Args:
        preferred: Explicit device choice ('auto', 'mps', 'cuda', 'cpu').
            If 'auto', the best available device is selected.

    Returns:
        Device string suitable for torch/sentence-transformers.
    """
    if preferred != "auto":
        if preferred == "mps" and torch.backends.mps.is_available():
            return "mps"
        if preferred == "cuda" and torch.cuda.is_available():
            return "cuda"
        if preferred == "cpu":
            return "cpu"
        logger.warning(
            "requested_device_unavailable",
            device=preferred,
            fallback="auto",
        )

    # Auto-detect: MPS > CUDA > CPU
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _resolve_task_type(task_type: str) -> str:
    """Resolve a task type string to a nomic-compatible task type.

    Accepts both nomic-native types (search_query, search_document) and
    legacy Gemini types (RETRIEVAL_QUERY, RETRIEVAL_DOCUMENT).

    Args:
        task_type: Task type string in either format.

    Returns:
        Nomic-compatible task type string.
    """
    if task_type in GEMINI_TO_NOMIC_TASK_MAP:
        return GEMINI_TO_NOMIC_TASK_MAP[task_type]
    return task_type


class LocalEmbeddingModel:
    """Local embedding model wrapping sentence-transformers with nomic-embed-text-v1.5.

    Features:
        - 768-dimensional embeddings (matches common vector DB defaults)
        - Task-type prefix handling for asymmetric retrieval
        - Lazy model loading on first embed call
        - Batch embedding support with configurable batch size
        - L2 normalization for consistent similarity scores
        - Automatic device selection (MPS > CUDA > CPU)

    Usage:
        settings = MemorySettings()
        model = LocalEmbeddingModel(settings)
        model.load()  # Optional -- happens automatically on first embed

        # Single embedding
        vec = model.embed("What is memory consolidation?", task_type="search_query")

        # Batch embeddings
        vecs = model.embed_batch(
            ["Kora loves her family", "Memory is important"],
            task_type="search_document",
        )
    """

    def __init__(
        self,
        settings: MemorySettings | None = None,
        *,
        device: str = "auto",
        trust_remote_code: bool = True,
    ) -> None:
        """Initialize the local embedding model wrapper.

        The model is NOT loaded until load() or the first embed call.

        Args:
            settings: MemorySettings providing model name and dimension.
                Falls back to defaults if None.
            device: Compute device ('auto', 'mps', 'cuda', 'cpu').
            trust_remote_code: Whether to trust remote code from HuggingFace.
                Required True for nomic models.
        """
        self.model_name = settings.embedding_model if settings else DEFAULT_MODEL_NAME
        self.dimension = settings.embedding_dims if settings else DEFAULT_DIMENSION
        self._requested_device = device
        self._trust_remote_code = trust_remote_code

        # Set after load()
        self._model: object | None = None
        self._device: str | None = None
        self._loaded: bool = False
        # Thread lock -- model.encode() is NOT thread-safe on MPS/CUDA
        self._encode_lock = threading.Lock()

    @property
    def device(self) -> str:
        """The actual device the model is loaded on."""
        if self._device is None:
            return _detect_device(self._requested_device)
        return self._device

    @property
    def is_loaded(self) -> bool:
        """Whether the model has been loaded into memory."""
        return self._loaded

    def load(self) -> None:
        """Load the sentence-transformers model into memory.

        This is called automatically on the first embed call if not
        called explicitly. Explicit loading is useful to control when
        the ~270MB model download and GPU memory allocation happens.

        Raises:
            ImportError: If sentence-transformers is not installed.
            RuntimeError: If model loading fails.
        """
        if self._loaded:
            return

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers not installed. "
                "Install with: pip install sentence-transformers"
            )

        self._device = _detect_device(self._requested_device)
        logger.info(
            "loading_embedding_model",
            model=self.model_name,
            device=self._device,
        )

        try:
            self._model = SentenceTransformer(
                self.model_name,
                trust_remote_code=self._trust_remote_code,
                device=self._device,
            )
            self._loaded = True
            logger.info(
                "embedding_model_loaded",
                model=self.model_name,
                device=self._device,
                dimension=self.dimension,
            )
        except Exception as e:
            logger.error(
                "embedding_model_load_failed",
                model=self.model_name,
                error=str(e),
            )
            raise RuntimeError(
                f"Failed to load embedding model '{self.model_name}': {e}"
            ) from e

    def unload(self) -> None:
        """Unload the model from memory and free GPU/RAM resources."""
        if not self._loaded:
            return

        self._model = None
        self._loaded = False
        self._device = None

        # Encourage garbage collection of GPU memory
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        logger.info("embedding_model_unloaded")

    def _ensure_loaded(self) -> None:
        """Ensure the model is loaded, loading lazily if needed."""
        if not hasattr(self, "_encode_lock") or self._encode_lock is None:
            self._encode_lock = threading.Lock()
        if not self._loaded:
            self.load()

    @staticmethod
    def _apply_prefix(text: str, task_type: str) -> str:
        """Apply the nomic task-type prefix to input text.

        The nomic-embed-text-v1.5 model uses text prefixes to distinguish
        between document storage and query retrieval, improving asymmetric
        search quality.

        Args:
            text: Raw input text.
            task_type: Nomic task type (search_document, search_query, etc.).

        Returns:
            Text with the appropriate prefix prepended.
        """
        resolved = _resolve_task_type(task_type)
        prefix = TASK_PREFIX_MAP.get(resolved, "")
        if prefix:
            return prefix + text
        return text

    def embed(
        self,
        text: str,
        task_type: str = "search_query",
        normalize: bool = True,
    ) -> list[float]:
        """Generate an embedding for a single text.

        Args:
            text: Input text to embed.
            task_type: Task type for prefix selection
                ('search_query', 'search_document', or Gemini-style
                 'RETRIEVAL_QUERY', 'RETRIEVAL_DOCUMENT').
            normalize: Whether to L2 normalize the output vector.

        Returns:
            Embedding vector as a list of floats (length = self.dimension).
        """
        self._ensure_loaded()

        prefixed = self._apply_prefix(text, task_type)
        # sentence-transformers encode() is NOT thread-safe on MPS/CUDA
        with self._encode_lock:
            embedding = self._model.encode(  # type: ignore[union-attr]
                prefixed,
                convert_to_numpy=True,
                normalize_embeddings=False,  # We do our own normalization
                show_progress_bar=False,
            )

        result = embedding.tolist()

        if normalize:
            result = _l2_normalize(result)

        return result

    def embed_batch(
        self,
        texts: list[str],
        task_type: str = "search_document",
        batch_size: int = 64,
        normalize: bool = True,
    ) -> list[list[float]]:
        """Generate embeddings for multiple texts in a batch.

        Uses sentence-transformers internal batching for efficient
        GPU utilization.

        Args:
            texts: List of input texts to embed.
            task_type: Task type for prefix selection.
            batch_size: Internal batch size for the model encoder.
            normalize: Whether to L2 normalize each output vector.

        Returns:
            List of embedding vectors, one per input text.
        """
        if not texts:
            return []

        self._ensure_loaded()

        prefixed = [self._apply_prefix(t, task_type) for t in texts]

        # sentence-transformers encode() is NOT thread-safe on MPS/CUDA
        with self._encode_lock:
            embeddings = self._model.encode(  # type: ignore[union-attr]
                prefixed,
                batch_size=batch_size,
                convert_to_numpy=True,
                normalize_embeddings=False,
                show_progress_bar=False,
            )

        results: list[list[float]] = []
        for emb in embeddings:
            vec = emb.tolist()
            if normalize:
                vec = _l2_normalize(vec)
            results.append(vec)

        return results
