from aicodebot.config import read_config
from aicodebot.helpers import logger
from langchain import HuggingFaceHub
from langchain.chains import LLMChain
from langchain.chat_models import ChatOpenAI
from langchain.memory import ConversationTokenBufferMemory
from openai.api_resources import engine
import functools, openai, os, tiktoken

DEFAULT_RESPONSE_TOKENS = 512
DEFAULT_MEMORY_TOKENS = DEFAULT_RESPONSE_TOKENS * 2
DEFAULT_CONTEXT_TOKENS = DEFAULT_RESPONSE_TOKENS * 4
PRECISE_TEMPERATURE = 0.05
CREATIVE_TEMPERATURE = 0.6


class LanguageModelManager:
    """A class for interacting with language models."""

    provider = model_name = _memory = None

    OPENAI = "OpenAI"
    OPENROUTER = "OpenRouter"
    HUGGINGFACE_HUB = "HuggingFace Hub"
    PROVIDERS = [OPENAI, OPENROUTER, HUGGINGFACE_HUB]
    DEFAULT_MODEL = "gpt-4"
    DEFAULT_PROVIDER = OPENAI

    def __init__(self, model_name=None, provider=None):
        self.model_name = model_name
        self.provider = provider
        if not self.model_name:
            self.read_model_config()

    # --------------------------------- Factories -------------------------------- #

    def chain_factory(
        self,
        prompt,
        response_token_size=None,
        temperature=PRECISE_TEMPERATURE,
        streaming=False,
        callbacks=None,
        chat_history=False,
    ):
        language_model = self.model_factory(response_token_size, temperature, streaming, callbacks)
        if chat_history:
            memory = self.get_memory(language_model)
        else:
            memory = None
        return LLMChain(llm=language_model, prompt=prompt, memory=memory)

    def model_factory(
        self,
        response_token_size=None,
        temperature=PRECISE_TEMPERATURE,
        streaming=False,
        callbacks=None,
    ):
        """Get a model object for the specified model name."""

        # We support multiple approaches for using language models:
        # 1. OpenAI API
        # 2. Open Router API
        # 3. SOON Hugging Face API
        # 4. FUTURE - Local models

        provider, model_name = self.read_model_config()

        if provider == self.OPENAI:
            return self.get_openai_model(
                model_name,
                response_token_size=response_token_size,
                temperature=temperature,
                streaming=streaming,
                callbacks=callbacks,
            )
        elif provider == self.OPENROUTER:
            return self.get_openrouter_model(
                model_name,
                response_token_size=response_token_size,
                temperature=temperature,
                streaming=streaming,
                callbacks=callbacks,
            )
        elif provider == self.HUGGINGFACE_HUB:
            return self.get_huggingface_hub_model(
                model_name,
                response_token_size=response_token_size,
                temperature=temperature,
                streaming=streaming,
                callbacks=callbacks,
            )
        else:  # pragma: no cover
            raise ValueError(f"Provider {provider} is not one of: {self.PROVIDERS}")

    def get_huggingface_hub_model(
        self,
        model_name,
        response_token_size=None,
        temperature=PRECISE_TEMPERATURE,
        streaming=False,
        callbacks=None,
    ):
        # Support for HuggingFace Hub. It works. Barely. Don't expect good results.
        # And there is no streaming.
        # If you want to play with it, add the following to your ~/.aicodebot.yaml file:
        """
        language_model_provider: HuggingFace Hub
        huggingface_api_key: hf_xxxxx
        # This is about the only model I could get to work
        language_model: google/flan-t5-xxl
        """

        api_key = self.get_api_key("huggingface_api_key")
        return HuggingFaceHub(
            huggingfacehub_api_token=api_key,
            repo_id=model_name,
            model_kwargs={
                "temperature": temperature,
                "max_length": response_token_size,
                "max_new_tokens": response_token_size,
            },
        )

    def get_memory(self, llm, token_limit=DEFAULT_MEMORY_TOKENS, memory_key="chat_history", input_key="task"):
        """Initializes a memory object with the specified parameters."""
        if not self._memory:  # Re-use the same memory object
            self._memory = ConversationTokenBufferMemory(
                memory_key=memory_key, input_key=input_key, llm=llm, max_token_limit=token_limit
            )
        return self._memory

    def get_openai_model(
        self,
        model_name,
        response_token_size=None,
        temperature=PRECISE_TEMPERATURE,
        streaming=False,
        callbacks=None,
    ):
        """Get an OpenAI model object for the specified model name."""
        api_key = self.get_api_key("OPENAI_API_KEY")

        return ChatOpenAI(
            openai_api_key=api_key,
            model=model_name,
            max_tokens=response_token_size,
            temperature=temperature,
            streaming=streaming,
            callbacks=callbacks,
        )

    def get_api_key(self, key_name):
        # Read the api key from either the environment or the config file
        key_name_upper = key_name.upper()
        api_key = os.getenv(key_name)
        if api_key:
            return api_key
        else:
            config = read_config()
            key_name_lower = key_name.lower()
            # Try both upper and lower case from the config file
            if key_name_lower in config:
                return config[key_name_lower]
            elif key_name_upper in config:
                return config[key_name_upper]
            else:
                return None

    def get_openrouter_model(
        self,
        model_name,
        response_token_size=None,
        temperature=PRECISE_TEMPERATURE,
        streaming=False,
        callbacks=None,
    ):
        api_key = self.get_api_key("OPENROUTER_API_KEY")

        # Set the API base to the Open Router API, and set special headers that are required
        api_base = "https://openrouter.ai/api/v1"
        headers = {"HTTP-Referer": "https://aicodebot.dev", "X-Title": "AICodeBot"}

        return ChatOpenAI(
            openai_api_key=api_key,
            openai_api_base=api_base,
            model=model_name,
            max_tokens=response_token_size,
            temperature=temperature,
            streaming=streaming,
            callbacks=callbacks,
            # In order to get ConversationBufferMemory to work, we need to set the tiktoken model name
            tiktoken_model_name=self.tiktoken_model_name,
            model_kwargs={"headers": headers},
        )

    def get_model_token_limit(self, model_name):
        model_token_limits = {
            "openai/gpt-4": 8192,
            "openai/gpt-4-32k": 32768,
            "anthropic/claude-2": 100_000,
            "gpt-4": 8192,
            "gpt-4-32k": 32768,
            "gpt-3.5-turbo": 4096,
            "gpt-3.5-turbo-16k": 16384,
        }
        if model_name in model_token_limits:
            return model_token_limits[model_name]
        else:
            raise ValueError(f"Model {model_name} not found")

    def get_token_size(self, text):
        """Get the number of tokens in a string using the tiktoken library."""
        encoding = tiktoken.encoding_for_model(self.tiktoken_model_name)
        tokens = encoding.encode(text)
        return len(tokens)

    def read_model_config(self):
        # Figure out which model to use, based on the config file or environment variables
        config = read_config()
        self.provider = os.getenv(
            "AICODEBOT_MODEL_PROVIDER", config.get("language_model_provider", self.DEFAULT_PROVIDER)
        )
        self.model_name = os.getenv("AICODEBOT_MODEL", config.get("language_model", self.DEFAULT_MODEL))

        # --------------------------- API key verification --------------------------- #
        if self.provider == self.OPENAI:
            key_name = "OPENAI_API_KEY"
        elif self.provider == self.OPENROUTER:
            key_name = "OPENROUTER_API_KEY"
        elif self.provider == self.HUGGINGFACE_HUB:
            key_name = "HUGGINGFACE_API_KEY"
        else:
            raise ValueError(f"Unrecognized provider: {self.provider}")

        if not self.get_api_key(key_name):
            raise ValueError(
                f"In order to use {self.provider}, you must set the {key_name} in your environment or config file"
            )

        # TODO: Verify the model

        return self.provider, self.model_name

    @functools.lru_cache  # cache so we only make the API call once
    @staticmethod
    def openai_supported_engines():
        """Get a list of the models supported by the OpenAI API key."""
        config = read_config()
        openai.api_key = config["openai_api_key"]
        engines = engine.Engine.list()
        out = [engine.id for engine in engines.data]
        logger.trace(f"OpenAI supported engines: {out}")
        return out

    @property
    def tiktoken_model_name(self):
        if "/" in self.model_name:
            if self.model_name.startswith("openai/"):
                # For OpenAI models, this is as simple as stripping the prefix "openai/" from the model name
                return self.model_name.replace("openai/", "")
            else:
                # For non-OpenAI models, we set the model name to "gpt-4" for now. Seems to work.
                # Tested with anthropic/claude2
                return self.DEFAULT_MODEL
        else:
            return self.model_name


def token_size(text):
    # Shortcut
    return LanguageModelManager().get_token_size(text)
