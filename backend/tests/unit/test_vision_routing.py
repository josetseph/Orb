"""Images are read by the ingestion model: projector lookup and the wire shape."""

from pathlib import Path

from app.services.local_models import find_mmproj, mmproj_hf_path


class TestProjectorName:
    def test_bartowski_layout(self):
        assert (
            mmproj_hf_path("bartowski/google_gemma-4-E4B-it-GGUF/google_gemma-4-E4B-it-Q4_K_M.gguf")
            == "bartowski/google_gemma-4-E4B-it-GGUF/mmproj-google_gemma-4-E4B-it-f16.gguf"
        )

    def test_iq_and_f16_suffixes_are_stripped(self):
        assert mmproj_hf_path("r/x-IQ4_XS.gguf").endswith("/mmproj-x-f16.gguf")
        assert mmproj_hf_path("r/x-F16.gguf").endswith("/mmproj-x-f16.gguf")

    def test_qwen_layout(self):
        assert (
            mmproj_hf_path("bartowski/Qwen_Qwen3.5-4B-GGUF/Qwen_Qwen3.5-4B-Q4_K_M.gguf")
            == "bartowski/Qwen_Qwen3.5-4B-GGUF/mmproj-Qwen_Qwen3.5-4B-f16.gguf"
        )


class TestFindProjector:
    def test_matches_by_model_stem(self, tmp_path: Path):
        chat = tmp_path / "google_gemma-4-E4B-it-Q4_K_M.gguf"
        chat.write_bytes(b"x")
        (tmp_path / "mmproj-Qwen_Qwen3.5-4B-f16.gguf").write_bytes(b"x")
        mine = tmp_path / "mmproj-google_gemma-4-E4B-it-f16.gguf"
        mine.write_bytes(b"x")
        assert find_mmproj(chat) == mine

    def test_single_projector_in_folder_is_taken(self, tmp_path: Path):
        chat = tmp_path / "custom-model.gguf"
        chat.write_bytes(b"x")
        only = tmp_path / "mmproj-whatever.gguf"
        only.write_bytes(b"x")
        assert find_mmproj(chat) == only

    def test_ambiguous_projectors_are_not_guessed(self, tmp_path: Path):
        chat = tmp_path / "custom-model.gguf"
        chat.write_bytes(b"x")
        (tmp_path / "mmproj-a.gguf").write_bytes(b"x")
        (tmp_path / "mmproj-b.gguf").write_bytes(b"x")
        assert find_mmproj(chat) is None

    def test_no_projector(self, tmp_path: Path):
        chat = tmp_path / "m.gguf"
        chat.write_bytes(b"x")
        assert find_mmproj(chat) is None


class TestImagePayload:
    def test_downscales_and_encodes_jpeg(self, tmp_path: Path):
        from PIL import Image

        from app.core.config import settings
        from app.services.multimedia import image_data_url

        big = tmp_path / "big.png"
        Image.new("RGBA", (3000, 2000), (10, 20, 30, 255)).save(big)
        settings.IMAGE_DESCRIBE_MAX_PIXELS = 100_000
        url = image_data_url(str(big))
        assert url.startswith("data:image/jpeg;base64,")
        import base64
        import io

        decoded = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
        assert decoded.width * decoded.height <= 100_000
        assert decoded.mode == "RGB"
