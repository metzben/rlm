from pathlib import Path
import os


class Config:
    def __init__(self, envfile: str | Path = ".env") -> None:
        self.load_env(envfile)

        self.registry = os.getenv("REGISTRY")

    def load_env(self, envfile: str | Path) -> None:
        env_path = Path(envfile)

        with env_path.open("r", encoding="utf-8") as file:
            for line in file:
                # strip spaces
                line = line.strip()
                # check for blank lines and comments
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()

                # remove quotes if there are any
                if (
                    len(value) >= 2
                    and value[0] == value[-1]
                    and value[0] in ("'", '"')
                ):
                    value = value[1:-1]

                if not value:
                    continue

                quote_chars = ("'", '"')
                if value[0] in quote_chars:
                    opening_quote = value[0]
                    if len(value) < 2 or value[-1] != opening_quote:
                        continue

                if len(key) > 0 and len(value) > 0:
                    os.environ.setdefault(key, value)
