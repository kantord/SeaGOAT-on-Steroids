#!/bin/env python

import atexit
from typing import Optional
import click
import datetime
import logging
import os
import requests
import sys
import yaml
import json

from pathlib import Path
from seagoat.utils.server import get_server_info  # type: ignore
from seagoat.cli import query_server, rewrite_full_paths_to_use_local_path  # type: ignore
from prompt_toolkit import PromptSession, HTML
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.logging import RichHandler
from rich.markdown import Markdown
from halo import Halo
from xdg_base_dirs import xdg_config_home

BASE = Path(xdg_config_home(), "seagoat-on-steroids")
CONFIG_FILE = BASE / "config.yaml"
HISTORY_FILE = BASE / "history"
SAVE_FOLDER = BASE / "session-history"
SAVE_FILE = (
    "chatgpt-session-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".json"
)
BASE_ENDPOINT = "https://api.openai.com/v1"
ENV_VAR = "OPENAI_API_KEY"

PRICING_RATE = {
    "gpt-3.5-turbo": {"prompt": 0.001, "completion": 0.002},
    "gpt-3.5-turbo-1106": {"prompt": 0.001, "completion": 0.002},
    "gpt-3.5-turbo-0613": {"prompt": 0.001, "completion": 0.002},
    "gpt-3.5-turbo-16k": {"prompt": 0.001, "completion": 0.002},
    "gpt-4": {"prompt": 0.03, "completion": 0.06},
    "gpt-4-0613": {"prompt": 0.03, "completion": 0.06},
    "gpt-4-32k": {"prompt": 0.06, "completion": 0.12},
    "gpt-4-32k-0613": {"prompt": 0.06, "completion": 0.12},
    "gpt-4-1106-preview": {"prompt": 0.01, "completion": 0.03},
}

logger = logging.getLogger("rich")
logging.basicConfig(
    level="INFO",
    format="%(message)s",
    handlers=[
        RichHandler(show_time=False, show_level=False, show_path=False, markup=True)
    ],
)


# Initialize the messages history list
# It's mandatory to pass it at each API call in order to have a conversation
messages = []
# Initialize the token counters
prompt_tokens = 0
completion_tokens = 0
# Initialize the console
console = Console()


def load_config(config_file: Path) -> dict:
    """
    Read a YAML config file and returns it's content as a dictionary
    """
    if not Path(config_file).exists():
        config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, "w") as file:
            file.write(
                'api-key: "INSERT API KEY HERE"\n' + 'model: "gpt-3.5-turbo-16k"\n'
                "temperature: 1\n"
                "#max_tokens: 500\n"
                "markdown: true\n"
            )
        # console.print(f"New config file initialized: [green bold]{config_file}")

    with open(config_file) as file:
        config = yaml.load(file, Loader=yaml.FullLoader)

    return config


def load_history_data(history_file: str) -> dict:
    """
    Read a session history json file and return its content
    """
    with open(history_file) as file:
        content = json.loads(file.read())

    return content


def get_last_save_file() -> Optional[str]:
    """
    Return the timestamp of the last saved session
    """
    files = [f for f in os.listdir(SAVE_FOLDER) if f.endswith(".json")]
    if files:
        ts = [f.replace("chatgpt-session-", "").replace(".json", "") for f in files]
        ts.sort()
        return ts[-1]

    return None


def create_save_folder() -> None:
    """
    Create the session history folder if not exists
    """
    if not os.path.exists(SAVE_FOLDER):
        os.mkdir(SAVE_FOLDER)


def save_history(
    model: str, messages: list, prompt_tokens: int, completion_tokens: int
) -> None:
    """
    Save the conversation history in JSON format
    """
    with open(os.path.join(SAVE_FOLDER, SAVE_FILE), "w") as f:
        json.dump(
            {
                "model": model,
                "messages": messages,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
            f,
            indent=4,
            ensure_ascii=False,
        )


def add_markdown_system_message() -> None:
    """
    Try to force ChatGPT to always respond with well formatted code blocks and tables if markdown is enabled.
    """
    instruction = "Always use code blocks with the appropriate language tags. If asked for a table always format it using Markdown syntax."
    messages.append({"role": "system", "content": instruction})


def calculate_expense(
    prompt_tokens: int,
    completion_tokens: int,
    prompt_pricing: float,
    completion_pricing: float,
) -> str:
    """
    Calculate the expense, given the number of tokens and the pricing rates
    """
    expense = ((prompt_tokens / 1000) * prompt_pricing) + (
        (completion_tokens / 1000) * completion_pricing
    )

    # Format to display in decimal notation rounded to 6 decimals
    expense = "{:.6f}".format(round(expense, 6))

    return expense


def display_expense(model: str) -> None:
    """
    Given the model used, display total tokens used and estimated expense
    """
    logger.info(
        f"\nTotal tokens used: [green bold]{prompt_tokens + completion_tokens}",
        extra={"highlighter": None},
    )

    if model in PRICING_RATE:
        total_expense = calculate_expense(
            prompt_tokens,
            completion_tokens,
            PRICING_RATE[model]["prompt"],
            PRICING_RATE[model]["completion"],
        )
        logger.info(
            f"Estimated expense: [green bold]${total_expense}",
            extra={"highlighter": None},
        )
    else:
        logger.warning(
            f"[red bold]No expense estimate available for model {model}",
            extra={"highlighter": None},
        )


def get_context_from_seagoat(seagoat_address: str, message: str, repo: str) -> str:
    seagoat_results = query_server(
        message,
        seagoat_address,
        max_results=75,
        context_above=3,
        context_below=3,
    )
    seagoat_results = rewrite_full_paths_to_use_local_path(repo, seagoat_results)

    result_lines = []

    for result in seagoat_results:
        for block in result["blocks"]:
            first_line_number = block["lines"][0]["line"]  # type: ignore
            last_line_number = block["lines"][-1]["line"]  # type: ignore
            result_lines.append(f"File: {result['path']}")
            result_lines.append(f"Lines: {first_line_number}-{last_line_number}")
            result_lines.append("")
            result_lines.append("```")
            for line in block["lines"]:  # type: ignore
                result_lines.append(line["lineText"])  # type: ignore
            result_lines.append("```")

    return "\n".join(result_lines)


def start_prompt(
    session: PromptSession, config: dict, seagoat_address: str, repo: str
) -> None:
    """
    Ask the user for input, build the request and perform it
    """

    # TODO: Refactor to avoid a global variables
    global prompt_tokens, completion_tokens

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['api-key']}",
    }

    message = ""

    if config["non_interactive"]:
        message = sys.stdin.read()
    else:
        message = session.prompt(
            HTML(f"<b>[{prompt_tokens + completion_tokens}] >>> </b>")
        )

    context_from_seagoat = get_context_from_seagoat(seagoat_address, message, repo)
    message = f"""Answer the users query with the following code snippets from their code repository as your context:
{context_from_seagoat}
====
Keep in mind that you only need to use this context if it's actually relevant to the context.
Feel free to mention different options, and if possible mention even the code line numbers.
Finally, this is the actual user query that you have to answer: "{message}"
    """

    if message.lower() == "/q":
        raise EOFError
    if message.lower() == "":
        raise KeyboardInterrupt

    messages.append({"role": "user", "content": message})

    # Base body parameters
    body = {
        "model": config["model"],
        "temperature": config["temperature"],
        "messages": messages,
    }
    # Optional parameters
    if "max_tokens" in config:
        body["max_tokens"] = config["max_tokens"]
    if config["json_mode"]:
        body["response_format"] = {"type": "json_object"}

    with Halo(text="Thinking", spinner="dots"):
        try:
            r = requests.post(
                f"{BASE_ENDPOINT}/chat/completions", headers=headers, json=body
            )
        except requests.ConnectionError:
            logger.error(
                "[red bold]Connection error, try again...", extra={"highlighter": None}
            )
            messages.pop()
            raise KeyboardInterrupt
        except requests.Timeout:
            logger.error(
                "[red bold]Connection timed out, try again...", extra={"highlighter": None}
            )
            messages.pop()
            raise KeyboardInterrupt

    match r.status_code:
        case 200:
            response = r.json()

            message_response = response["choices"][0]["message"]
            usage_response = response["usage"]

            if not config["non_interactive"]:
                console.line()
            if config["markdown"]:
                console.print(Markdown(message_response["content"].strip()))
            else:
                print(message_response["content"].strip())
            if not config["non_interactive"]:
                console.line()

            # Update message history and token counters
            messages.append(message_response)
            prompt_tokens += usage_response["prompt_tokens"]
            completion_tokens += usage_response["completion_tokens"]
            save_history(config["model"], messages, prompt_tokens, completion_tokens)

            if config["non_interactive"]:
                # In non-interactive mode there is no looping back for a second prompt, you're done.
                raise EOFError

        case 400:
            response = r.json()
            if "error" in response:
                if response["error"]["code"] == "context_length_exceeded":
                    logger.error(
                        "[red bold]Maximum context length exceeded",
                        extra={"highlighter": None},
                    )
                    raise EOFError
                    # TODO: Develop a better strategy to manage this case
            logger.error("[red bold]Invalid request", extra={"highlighter": None})
            raise EOFError

        case 401:
            logger.error("[red bold]Invalid API Key", extra={"highlighter": None})
            raise EOFError

        case 429:
            logger.error(
                "[red bold]Rate limit or maximum monthly limit exceeded",
                extra={"highlighter": None},
            )
            messages.pop()
            raise KeyboardInterrupt

        case 500:
            logger.error(
                "[red bold]Internal server error, check https://status.openai.com",
                extra={"highlighter": None},
            )
            messages.pop()
            raise KeyboardInterrupt

        case 502 | 503:
            logger.error(
                "[red bold]The server seems to be overloaded, try again",
                extra={"highlighter": None},
            )
            messages.pop()
            raise KeyboardInterrupt

        case _:
            logger.error(
                f"[red bold]Unknown error, status code {r.status_code}",
                extra={"highlighter": None},
            )
            logger.error(r.json(), extra={"highlighter": None})
            raise EOFError


@click.command()
@click.option(
    "-c",
    "--context",
    "context",
    type=click.File("r"),
    help="Path to a context file",
    multiple=True,
)
@click.option("-k", "--key", "api_key", help="Set the API Key")
@click.option("-m", "--model", "model", help="Set the model")
@click.option(
    "-ml", "--multiline", "multiline", is_flag=True, help="Use the multiline input mode"
)
@click.option(
    "-r",
    "--restore",
    "restore",
    help="Restore a previous chat session (input format: YYYYMMDD-hhmmss or 'last')",
)
@click.option(
    "-n",
    "--non-interactive",
    "non_interactive",
    is_flag=True,
    help="Non interactive/command mode for piping",
)
@click.option(
    "-j", "--json", "json_mode", is_flag=True, help="Activate json response mode"
)
@click.argument("repo", type=click.Path(exists=True), default=os.getcwd())
def main(
    context, api_key, model, multiline, restore, non_interactive, json_mode, repo
) -> None:
    seagoat_server_info = get_server_info(repo)
    seagoat_address = seagoat_server_info["address"]
    # If non interactive suppress the logging messages
    if non_interactive:
        logger.setLevel("ERROR")

    logger.info("[bold]ChatGPT CLI", extra={"highlighter": None})

    history = FileHistory(str(HISTORY_FILE))

    if multiline:
        session = PromptSession(history=history, multiline=True)
    else:
        session = PromptSession(history=history)

    try:
        config = load_config(CONFIG_FILE)
    except FileNotFoundError:
        logger.error(
            "[red bold]Configuration file not found", extra={"highlighter": None}
        )
        sys.exit(1)

    create_save_folder()

    # Order of precedence for API Key configuration:
    # Command line option > Environment variable > Configuration file

    # If the environment variable is set overwrite the configuration
    if os.environ.get(ENV_VAR):
        config["api-key"] = os.environ[ENV_VAR].strip()
    # If the --key command line argument is used overwrite the configuration
    if api_key:
        config["api-key"] = api_key.strip()
    # If the --model command line argument is used overwrite the configuration
    if model:
        config["model"] = model.strip()

    config["non_interactive"] = non_interactive

    # Do not emit markdown in this case; ctrl character formatting interferes in several contexts including json
    # output.
    if config["non_interactive"]:
        config["markdown"] = False

    config["json_mode"] = json_mode

    # Run the display expense function when exiting the script
    atexit.register(display_expense, model=config["model"])

    logger.info(
        f"Model in use: [green bold]{config['model']}", extra={"highlighter": None}
    )

    # Add the system message for code blocks in case markdown is enabled in the config file
    if config["markdown"]:
        add_markdown_system_message()

    # Context from the command line option
    if context:
        for c in context:
            logger.info(
                f"Context file: [green bold]{c.name}", extra={"highlighter": None}
            )
            messages.append({"role": "system", "content": c.read().strip()})

    # Restore a previous session
    if restore:
        if restore == "last":
            last_session = get_last_save_file()
            restore_file = f"chatgpt-session-{last_session}.json"
        else:
            restore_file = f"chatgpt-session-{restore}.json"
        try:
            global prompt_tokens, completion_tokens
            # If this feature is used --context is cleared
            messages.clear()
            history_data = load_history_data(os.path.join(SAVE_FOLDER, restore_file))
            for message in history_data["messages"]:
                messages.append(message)
            prompt_tokens += history_data["prompt_tokens"]
            completion_tokens += history_data["completion_tokens"]
            logger.info(
                f"Restored session: [bold green]{restore}",
                extra={"highlighter": None},
            )
        except FileNotFoundError:
            logger.error(
                f"[red bold]File {restore_file} not found", extra={"highlighter": None}
            )

    if json_mode:
        logger.info(
            "JSON response mode is active. Your message should contain the [bold]'json'[/bold] word.",
            extra={"highlighter": None},
        )

    if not non_interactive:
        console.rule()

    while True:
        try:
            start_prompt(session, config, seagoat_address, repo)
        except KeyboardInterrupt:
            continue
        except EOFError:
            break


if __name__ == "__main__":
    main()
