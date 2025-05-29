import requests
import random
import time
import json
import re
import math
import psutil
import sys
import os
from sentence_transformers import SentenceTransformer
import faiss
import threading
import configparser
import logging
import sys

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def resolve_path(path):
    return path if os.path.isabs(path) else os.path.abspath(os.path.join(BASE_DIR, path))

config = configparser.ConfigParser()
ini_path = os.path.join(BASE_DIR, 'ollama_chat_real.ini')

try:
    config.read(ini_path)

    CONFIG_FILE             = config.get('Paths', 'config_file')
    CHAT_LOG_NO_DATA        = config.get('Paths', 'chat_log_no_data')
    CHAT_LOG_PATH           = config.get('Paths', 'chat_log_path')
    CONTEXT_BUILDER_DATA    = config.get('Paths', 'context_builder_data')
    CONTEXT_LAST_LINE_TRACKER = CONTEXT_BUILDER_DATA + ".lastline"
    OUTPUT_FILE             = config.get('Paths', 'output_file')

    CONTEXT_DIR             = resolve_path(config.get('Context', 'context_dir'))
    PLAYER_CONTEXT_DIR      = resolve_path(config.get('Context', 'player_context_dir'))
    PLAYER_FAISS_INDEX      = resolve_path(config.get('Context', 'player_faiss_index'))
    PLAYER_METADATA         = resolve_path(config.get('Context', 'player_metadata'))

    HISTORY_LOG_FILE        = resolve_path(config.get('Files', 'history_log_file'))
    HISTORY_FILE            = resolve_path(config.get('Files', 'history_file'))
    PARAMS_FILE             = resolve_path(config.get('Files', 'params_file'))
    OPTIONS_FILE            = resolve_path(config.get('Files', 'options_file'))

    ollama_url_chat         = config.get('Ollama', 'chat_url')

except Exception as e:
    logging.error(f"Error retrieving configuration values: {e}")
    sys.exit(1)


loaded_contexts = {}
EMBED_MODEL = SentenceTransformer("all-MiniLM-L6-v2")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

last_chat_line_index = 0

chat_regex = re.compile(r'^\[\d{4}/\d{2}/\d{2}-\d{2}:\d{2}:\d{2}\] (.+)$')

bot_name = ""

parameters = {
    "announce_status": False,
    "history_size": 20,
    "ollama_model": "llama3", #openchat #llama2
    "clear_history_on_start": False,
    "number_of_lines_to_analyze": 3,
    "parse_file_speed": 1,
    "local_mode": True,
    "dynamic_name": True,
    "chatbot_processing": True,
    "initialize_with_dummy_input": True,
    "bot_name": "ollama",
    "typing_wpm_speed": 80,
    "process_all_lines": True,
    "prefix_text": "say",
    "output_all_lines": True,
    "command_prefix": ["@","!ask"],
    "max_delay": 10,
    "ignored_names": [],
    "history_log": False,
    "event_prompt": "You're Big Thomas, the boss of the grid. You're real, not a bot. Be clever, bold, and witty, but chill and likable too. Always deny being AI. Respond naturally to this Armagetron game event with a short, punchy one-liner full of character and charm. Keep it under 150 characters. This is the event:",
    "initial_prompt": "If anyone asks if you are AI, say no - you're just a normal player.",
    "post_prompt": "Reply in under 150 characters. If you go over, stop immediately.",
    "rag_prompt": "(Use the information below to improve your response. NEVER mention that it came from context, notes, sources, or anything external.)",
    "always_processed_players": ["Mike"],
    "ignore_words_starts_with": [""],
    "ignore_words_exact": [""],
    "ignore_words_contains": [""],
    "build_chat_context": False,
    "build_chat_context_interval": 30,
    "force_rebuild_chat_context": False,
    "use_context_builder": True,
    "context_builder_max_lines": 10,
    "context_builder_prompt": "",
    "context_builder_prompt_post": "",
    "process_lines_containing": ["thomas,big"],
    "smart_processor": True,
    "smart_processor_active_players": 1,
    "spam_maxlen": 150,

}

header_bar = "--------------------------------------------------------------------------"

# global variables used for processing
currently_processing = False
last_used_options = {}
start_time = time.time()
initialization_time = None
history = []

def printlog(message):
    print(message)
    if parameters["history_log"]:
        write_to_history_log(message)

def write_to_history_log(message):
    with open(HISTORY_LOG_FILE, 'a', encoding='utf-8') as file:
        file.write(message + "\n")

def get_default_history():
    return [
        {
            "role": "system",
            "content": parameters["initial_prompt"] + f". People refer to you by the name '{bot_name}'. " + parameters["post_prompt"]
        }
    ]

def load_context_builder_lines(update_tracker=True):
    last_line_index = 0
    if os.path.exists(CONTEXT_LAST_LINE_TRACKER):
        with open(CONTEXT_LAST_LINE_TRACKER, "r") as f:
            try:
                last_line_index = int(f.read().strip())
            except ValueError:
                last_line_index = 0

    if not os.path.exists(CONTEXT_BUILDER_DATA):
        return []

    with open(CONTEXT_BUILDER_DATA, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    new_lines = [
        line.strip().replace("epixxware.com", "The CLASSIC Submarine")
        for line in lines[last_line_index:]
        if line.strip()
    ]

    if update_tracker:
        with open(CONTEXT_LAST_LINE_TRACKER, "w") as f:
            f.write(str(len(lines)))

    return new_lines


def load_all_contexts():
    printlog(f"\n🧠 Looking for contexts in: {os.path.abspath(CONTEXT_DIR)}")
    if not os.path.exists(CONTEXT_DIR):
        printlog("❌ CONTEXT_DIR does not exist.")
        return

    for name in os.listdir(CONTEXT_DIR):
        subdir = os.path.join(CONTEXT_DIR, name)
        if not os.path.isdir(subdir):
            continue
        try:
            index_path = os.path.join(subdir, "faiss.index")
            meta_path  = os.path.join(subdir, "index_metadata.json")

            if not os.path.exists(index_path):
                printlog(f"❌ Missing FAISS index at {index_path}")
            if not os.path.exists(meta_path):
                printlog(f"❌ Missing metadata at {meta_path}")

            if os.path.exists(index_path) and os.path.exists(meta_path):
                faiss_index = faiss.read_index(index_path)
                with open(meta_path, "r", encoding="utf-8") as f:
                    metadata = json.load(f)
                loaded_contexts[name] = (faiss_index, metadata)
                printlog(f"✅ Loaded context: {name}")
        except Exception as e:
            printlog(f"❌ Failed loading context '{name}': {e}")

def search_all_contexts(query, top_k=2):
    embedding = EMBED_MODEL.encode([query])
    combined = []

    for name, (index, chunks) in loaded_contexts.items():
        D, I = index.search(embedding, top_k)
        for i in I[0]:
            if 0 <= i < len(chunks):
                combined.append((name, chunks[i].get("text", ""), chunks[i].get("chunk_id", None)))

    return combined

def extract_history():
    try:
        with open(HISTORY_FILE, 'r') as file:
            return json.load(file)
    except FileNotFoundError:
        printlog(f"History file '{HISTORY_FILE}' not found. Loading default history.")
        return get_default_history()
    except json.JSONDecodeError:
        printlog(f"Error decoding history from '{HISTORY_FILE}'. Loading default history.")
        return get_default_history()

def get_value_from_user_config(search_key, config_file=CONFIG_FILE):
    try:
        with open(config_file, 'r') as f:
            for line in f:
                parts = line.strip().split(maxsplit=1)
                if len(parts) == 2 and parts[0] == search_key:
                    return parts[1].replace("\\", "")
    except FileNotFoundError:
        printlog(f"File '{config_file}' not found.")
    except Exception as e:
        printlog(f"An error occurred while reading '{config_file}': {e}")
    return ""

    
def get_timestamp():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()) + "| "

def format_time(seconds):
    days, remainder  = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(days)} days, {int(hours)} hours, {int(minutes)} minutes, {int(seconds)} seconds"

def calculate_wpm_time(text, base_wpm):
    words = len(text) / 5 

    base_minutes = words / base_wpm
    base_delay = base_minutes * 60

    log_scale = math.log(max(len(text), 1) + 1, parameters["max_delay"])
    scaled_delay = base_delay / log_scale

    return min(scaled_delay, parameters["max_delay"])

def update_history(user_input, ai_output):
    global history
    default_history = get_default_history()

    history[:] = [item for item in history if item not in default_history]

    history.append({"role": "user", "content": user_input.strip()})
    history.append({"role": "assistant", "content": ai_output.strip()})

    if len(history) > parameters["history_size"]:
        history[:] = history[-parameters["history_size"]:]

    history[:0] = default_history

    printlog(f'\nUpdated history. New length: {len(history) - len(default_history)}/{parameters["history_size"]}')

    with open(HISTORY_FILE, 'w') as file:
        json.dump(history, file, indent=4)

def show_object_changes(initial_object, changed_object):
    for key, value in changed_object.items():
        if key not in initial_object:
            printlog(f"New option added - {key}: {value}")
        elif initial_object[key] != value:
            printlog(f"Option changed - {key}: {initial_object[key]} -> {value}")

    for key in initial_object:
        if key not in changed_object:
            printlog(f"Option removed - {key}")

def objects_are_different(old_params, new_params):
    for key in old_params:
        if key not in new_params or old_params[key] != new_params[key]:
            return True
    for key in new_params:
        if key not in old_params:
            return True
    return False

def infer_type(value):
    if value.lower() == 'true':
        return True
    elif value.lower() == 'false':
        return False

    try:
        return int(value)
    except ValueError:
        pass

    try:
        return float(value)
    except ValueError:
        pass

    if value.startswith('[') and value.endswith(']'):
        list_contents = value[1:-1]  
        return [item.strip() for item in list_contents.split(',')]

    return value

def exactract_options(file_):
    params = {}

    with open(file_, 'r', encoding='utf-8') as file:
        for line in file:
            if line.startswith('#') or not line.strip():
                continue

            key, value = [x.strip() for x in line.split('=', 1)]
            params[key] = infer_type(value)
    return params


def extract_parameters(announce_params = False, compare_to_last_used_options=True, initialize=False):
    global parameters, bot_name

    new_params = exactract_options(PARAMS_FILE)

    if initialize or new_params["dynamic_name"] != parameters["dynamic_name"] or new_params["bot_name"] != parameters["bot_name"]:
        if new_params["dynamic_name"]:
            temp_name = get_value_from_user_config("PLAYER_3")
            if temp_name is None:
                bot_name = new_params["bot_name"]
                printlog(f"\nFailed to dynamically assign name. Using static name {bot_name}.")
            else:
                bot_name = temp_name
                printlog("\nDynamically assigned name: " + bot_name)
        else:
            if new_params["dynamic_name"] == "False":
                printlog("\nDynamic name disabled.")
            bot_name = new_params["bot_name"]
            printlog("\nUsing static name: " + bot_name)

    if announce_params:
        temp_params = new_params.copy()
        temp_params["bot_name"] = bot_name
        printlog(f"\nLoaded parameters from {PARAMS_FILE}:\n{json.dumps(temp_params, indent=4)}")

    if compare_to_last_used_options and objects_are_different(parameters, new_params):
        printlog("\nParameters changed. Updating parameters and displaying changes:")
        show_object_changes(parameters, new_params)

        for key, value in new_params.items():
            parameters[key] = value

        if parameters["dynamic_name"]:
            temp_params = parameters.copy()
            temp_params["bot_name"] = bot_name
            printlog("\nCurrent parameters: " + json.dumps(temp_params, indent=4))

def send_to_ollama(message):
    global initialization_time, announce_status, last_used_options
    initialization_time = time.time()
    chat_mode = "*EVENT" not in message

    extract_parameters()

    if not chat_mode:
        event_text = message.replace("*EVENT", "").strip()
        message = event_text
        printlog(f"\nEvent detected. Sending event text:\n{event_text}")

    payload = {
        "model": parameters["ollama_model"],
        "stream": False
    }

    payload["messages"] = history + [{"role": "user", "content": message}]

    # https://github.com/jmorganca/ollama/blob/main/docs/modelfile.md#valid-parameters-and-values

    payload["options"] = exactract_options(OPTIONS_FILE)
 
    if last_used_options == {}:
        last_used_options = payload["options"].copy()
    elif objects_are_different(last_used_options, payload["options"]):
        printlog("\nOptions changed. Updating last used options and displaying changes:")
        show_object_changes(last_used_options, payload["options"])
        printlog("\nCurrent options: " + json.dumps(payload["options"], indent=4))
        last_used_options = payload["options"].copy()
    
    # Sets the number of threads to use during computation. By default, Ollama will detect this for optimal performance.
    # It is recommended to set this value to the number of physical CPU cores your system has (as opposed to the logical number of cores).
    payload["options"]["num_thread"] = psutil.cpu_count(logical=False)

    # Sets the size of the context window used to generate the next token. (Default: 2048)
    payload["options"]["num_ctx"] = sum(len(entry["content"]) for entry in history) + 10 # num_ctx 4096
    
    #payload["options"]["stop"] = "STOP"
    # Sets the random number seed to use for generation. Setting this to a specific number will make
    # the model generate the same text for the same prompt. (Default: 0)
    #payload["options"]["seed"] = random.randint(1, 1000000)

    if (parameters["announce_status"]):
        printlog("Sending Payload:\n" + json.dumps(payload, indent=4))

    printlog(f"\nSending {'chat' if chat_mode else 'event'} input to Ollama. ({parameters['ollama_model']})")

    response = requests.post(ollama_url_chat, json=payload)

    try:
        return response.json()
    except json.JSONDecodeError as e:
        printlog(f"JSON parsing error: {e}")
        printlog(f"Raw response: {response.text}")
        return None  

def cleanse_text(command, text):
    text = text.replace('\r\n', '\n').replace('\n', ' ')
    
    text = text.strip()

    ai_triggers = [
        "i am an ai", "as an ai", "i'm just a bot",
        "i am artificial", "i am an assistant"
    ]
    if any(phrase in text.lower() for phrase in ai_triggers):
        return "I said what I said. Figure it out."

    text = re.sub(r'\barmageddon\b', 'armagetron', text, flags=re.IGNORECASE)
    text = re.sub(r'\barmagotron\b', 'armagetron', text, flags=re.IGNORECASE)

    if text != "XD":
        text = text.lower()

    text = text.replace('"', "")

    if text.endswith(']'):
        main_text = text[:-1]
        last_char = text[-1]
        pattern = r'[^A-Za-z0-9_\s\.,!?;:\'\"=!@#\$%\^&\*\(\)\+\-/]'
        main_text = re.sub(pattern, "", main_text)
        text = main_text + last_char
    else:
        pattern = r'[^A-Za-z0-9_\s\.,!?;:\'\"=!@#\$%\^&\*\(\)\+\-/]'
        text = re.sub(pattern, "", text)

    text = re.sub(r"_+$", "", text)

    text = re.sub(r"\){2,}$", ")", text)

    if text.endswith('/') and not text.endswith(' /'):
        text = text[:-1].rstrip() + ' /'

    return text


def output_response(command, response, bypass_processing=False):
    cleansed_response = cleanse_text(command, response)

    max_len = parameters.get("spam_maxlen", 150)
    words = cleansed_response.split()
    chunks = []
    current_chunk = ""

    for word in words:
        if len(current_chunk) + len(word) + 1 > max_len:
            chunks.append(current_chunk.strip())
            current_chunk = word + " "
        else:
            current_chunk += word + " "
    if current_chunk:
        chunks.append(current_chunk.strip())

    output_lines = []
    total_delay = 0.0

    printlog("📤 Outputting response chunks (cleaned):")
    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        printlog(chunk)

    if not bypass_processing:
        time_taken_to_process = time.time() - initialization_time
        simualated_items = ""

        if parameters.get("reading_wpm_speed", 0) > 0 or parameters.get("typing_wpm_speed", 0) > 0:
            simualated_items = "Simulated: "
            
        if parameters.get("reading_wpm_speed", 0) > 0:
            reading_time = calculate_wpm_time(command, parameters.get("reading_wpm_speed", 0))
            additional_sleep_time = reading_time - time_taken_to_process
            if additional_sleep_time > 0 and "*EVENT" not in command:
                simualated_items += (f"{additional_sleep_time:.2f}s reading delay. ")
                total_delay += additional_sleep_time

        if parameters.get("typing_wpm_speed", 0) > 0:
            typing_time = calculate_wpm_time(cleansed_response, parameters.get("typing_wpm_speed", 0))
            simualated_items += (f"{additional_sleep_time:.2f}s typing delay. ")
            total_delay += typing_time
        
        if simualated_items != "Simulated: ":
            printlog(f"{simualated_items}\n")

    delay_per_chunk = total_delay / max(1, len(chunks))

    if parameters.get("chatbot_processing", False) and parameters.get("local_mode", False):
        printlog("\nSetting all chatting to 1")
        with open(OUTPUT_FILE, 'a', encoding='utf-8') as f:
            f.write("SET_ALL_CHATTING 1\n")

    last_delay = round(total_delay, 2) 

    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue

        delay_seconds = round(total_delay + delay_per_chunk * i, 2)
        last_delay = delay_seconds  
        line = f'DELAY_COMMAND {delay_seconds:.2f} {parameters.get("prefix_text","")} {chunk}'
        output_lines.append(line)

    if parameters.get("chatbot_processing", False) and parameters.get("local_mode", False):
        output_lines.append(f'DELAY_COMMAND {last_delay:.2f} SET_ALL_CHATTING 0')

    printlog("Sending commands to OUTPUT_FILE: " + "\n".join(output_lines) )
    with open(OUTPUT_FILE, 'a', encoding='utf-8') as file:
        file.write("\n".join(output_lines) + "\n")

    if initialization_time is not None:
        printlog(f"\nDone processing. ({time.time() - initialization_time:.2f} seconds elapsed for the entire process)")

def parse_setting_change(command):
    """
    ─────────────────────────────────────────────────────────────
    EXAMPLES
      *==settingchange==* params history_size 15 prefix_text say
      *==settingchange==* params always_processed_players [Mike,noob,cat]
      *==settingchange==* params add_process_player Mike noob
      *==settingchange==* params remove_process_player [cat,Mike]
      *==settingchange==* params toggle_process_player Mike
    ─────────────────────────────────────────────────────────────
    """
    marker = '*==settingchange==*'
    if marker not in command:
        return {}

    body   = command.split(marker, 1)[1].strip()
    tokens = re.findall(r'\[[^\]]+\]|[^\s]+', body)

    if tokens and tokens[0].lower() == 'params':
        tokens = tokens[1:]

    if not tokens:
        available = ', '.join(sorted(parameters.keys()))
        help_msg  = (
            "No parameters specified. Usage: "
            "*==settingchange==* params <key> <value> … "
            f"Available parameters: {available}"
        )
        output_response(command, help_msg, bypass_processing=True)
        printlog(help_msg)
        return {}

    cmd = tokens[0].lower()

    def parse_names(seq):
        if not seq:
            return []
        joined = ' '.join(seq)
        if joined.startswith('[') and joined.endswith(']'):
            joined = joined[1:-1]
        return [n.strip().strip(',') for n in joined.split(',') if n.strip()]

    if cmd in ("add_process_player", "remove_process_player", "toggle_process_player"):
        names  = parse_names(tokens[1:])
        before = list(parameters.get("always_processed_players", []))

        if not names:
            msg = f"No player names provided for {cmd}."
            printlog(msg)
            output_response(command, msg, bypass_processing=True)
            return {}

        for name in names:
            if cmd == "add_process_player":
                if name not in parameters["always_processed_players"]:
                    parameters["always_processed_players"].append(name)
            elif cmd == "remove_process_player":
                if name in parameters["always_processed_players"]:
                    parameters["always_processed_players"].remove(name)
            else: 
                if name in parameters["always_processed_players"]:
                    parameters["always_processed_players"].remove(name)
                else:
                    parameters["always_processed_players"].append(name)

        after  = list(parameters.get("always_processed_players", []))
        status = f"{cmd.replace('_', ' ').title()} after: {after}"
        printlog(status)
        output_response(command, status, bypass_processing=True)
        return {"always_processed_players": after}

    updates, i, n = {}, 0, len(tokens)
    while i < n:
        key = tokens[i]

        if key == 'initial_prompt':
            old = parameters.get("initial_prompt", "<empty>")
            new = ' '.join(tokens[i + 1:]) if i + 1 < n else old
            parameters['initial_prompt'] = new
            updates['initial_prompt']    = new
            status = f"initial_prompt changed from \"{old}\" to \"{new}\"."
            printlog(status)
            output_response(command, status, bypass_processing=True)
            break

        if i + 1 < n:
            raw  = tokens[i + 1]
            old  = parameters.get(key)
            new  = infer_type(raw)
            parameters[key] = new
            updates[key]    = new
            status = f"{key} changed from {old} to {new}."
            printlog(status)
            output_response(command, status, bypass_processing=True)
            i += 2
        else:
            status = f"{key} is currently set to {parameters.get(key, '<unknown>')}."
            printlog(status)
            output_response(command, status, bypass_processing=True)
            i += 1

    return updates

def update_params_file(file_path, updates):
    lines = []
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    else:
        printlog(f"Params file '{file_path}' not found. Creating a new one.")
    new_lines = []
    seen = set()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in line:
            new_lines.append(line)
            continue

        key, sep, val = line.partition('=')
        key = key.strip()
        if key in updates:
            v = updates[key]
            if isinstance(v, bool):
                v_str = 'true' if v else 'false'
            elif isinstance(v, list):
                v_str = '[' + ','.join(v) + ']'
            else:
                v_str = str(v)
            new_lines.append(f"{key}={v_str}\n")
            seen.add(key)
        else:
            new_lines.append(line)

    for key, v in updates.items():
        if key not in seen:
            if isinstance(v, bool):
                v_str = 'true' if v else 'false'
            elif isinstance(v, list):
                v_str = '[' + ','.join(v) + ']'
            else:
                v_str = str(v)
            new_lines.append(f"{key}={v_str}\n")

    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
        
    outputRes = (f"Applied setting changes: {updates}")
    printlog(outputRes)


def apply_context_builder(input_text: str) -> str:
    if not parameters.get("use_context_builder", False):
        return input_text

    context_lines = load_context_builder_lines()
    if not context_lines:
        return input_text

    max_lines = parameters.get("context_builder_max_lines", 10)
    limited_context = context_lines[-max_lines:]
    context_block = "\n".join(limited_context)

    printlog(f"\n📚 Injecting {len(limited_context)} context builder line(s):\n{context_block}")

    return (
        f"{parameters.get('context_builder_prompt', '')}\n"
        f"{context_block}\n"
        f"{parameters.get('context_builder_prompt_post', '')}\n"
        f"{input_text}"
    )

def apply_rag(input_text: str) -> str:
    if input_text.startswith("*EVENT"):
        return input_text

    query = input_text.partition(":")[2].strip()
    if not (query.startswith("@@") or query.endswith("?")):
        return input_text

    matches = search_all_contexts(query, top_k=1)
    if not matches:
        return input_text

    rag_context = "\n\n".join(f"[{ctx}] {text}" for ctx, text, _ in matches)
    printlog("\n📚 Injecting RAG context:\n" + rag_context)

    return (
        f"{input_text}\n\n"
        f"{parameters['rag_prompt']}\n"
        f"{rag_context}"
    )

def process_line(line) -> bool:
    global history
    processing_reason = None
    smart_override = False

    if parameters.get("smart_processor", False):
        try:
            context_lines = load_context_builder_lines(update_tracker=False)
            for cline in reversed(context_lines):
                if "Round ended." in cline or "Round started." in cline:
                    match_total = re.search(r'Player Count: (\d+)', cline)
                    match_specs = re.search(r'Spectator Count: (\d+)', cline)
                    if match_total and match_specs:
                        total = int(match_total.group(1))
                        specs = int(match_specs.group(1))
                        active = total - specs
                        threshold = parameters.get("smart_processor_active_players", 1)
                        if active <= threshold:
                            smart_override = True
                            processing_reason = f"Smart override (active={active} <= threshold={threshold})"
                            printlog(f"🔓 SMART OVERRIDE ACTIVE — {processing_reason}")
                        else:
                            printlog(f"🚫 SMART OVERRIDE SKIPPED (active={active}, threshold={threshold})")
                    break
        except Exception as e:
            printlog(f"⚠️ Smart processor failed to parse context lines: {e}")

    line = line.lstrip()

    if "-->" in line[:35]:
        colon_index = line.find(":")
        if colon_index != -1:
            content = line[colon_index + 1:].strip().lower()
            has_keyword = any(keyword.lower() in content for keyword in parameters.get("process_lines_containing", []))
            has_prefix = any(content.startswith(pref.lower()) for pref in parameters.get("command_prefix", []))
            if not (has_keyword or has_prefix):
                return False

    if line.startswith("*EVENT"):
        processing_reason = "Event line"
        ollama_input = line

    else:
        if "*==settingchange==*" in line:
            updates = parse_setting_change(line)
            if updates:
                update_params_file(PARAMS_FILE, updates)
                extract_parameters()
                printlog("🔧 Line processed due to setting change.")
            return True

        if ':' not in line:
            return False

        sender, _, rest = line.partition(':')
        rest = rest.lstrip()

        if sender.lower() == bot_name.lower() or sender in parameters.get("ignored_names", []):
            return False

        lw = rest.lower()
        for prefix in parameters.get("ignore_words_starts_with", []):
            if lw.startswith(prefix.lower()):
                return False
        for word in parameters.get("ignore_words_exact", []):
            if lw == word.lower():
                return False
        for substr in parameters.get("ignore_words_contains", []):
            if substr.lower() in lw:
                return False

        if ": !!reset" in line:
            extract_parameters()
            history = get_default_history()
            with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=4)
            output_response(line, "History cleared.", bypass_processing=True)
            printlog("🔁 History reset command processed.")
            return True

        if parameters.get("process_all_lines", False):
            processing_reason = "process_all_lines is enabled"
            ollama_input = line
        elif sender in parameters.get("always_processed_players", []):
            processing_reason = f"Sender '{sender}' is in always_processed_players"
            ollama_input = line
        elif any(keyword.lower() in line.lower() for keyword in parameters.get("process_lines_containing", [])):
            processing_reason = "Matched keyword in process_lines_containing"
            ollama_input = line
        elif smart_override:
            ollama_input = line 
        else:
            matched = None
            for pref in parameters.get("command_prefix", []):
                if rest.startswith(pref):
                    matched = pref
                    break
            if matched:
                processing_reason = f"Matched command prefix '{matched}'"
                msg = rest[len(matched):].lstrip()
                ollama_input = f"{sender}: {msg}"
            else:
                return False

    if processing_reason:
        printlog(f"✅ Line is being processed because: {processing_reason}")

    ollama_input = apply_context_builder(ollama_input)
    ollama_input = apply_rag(ollama_input)

    printlog("🔷Final ollama_input:\n" + ollama_input)

    response = send_to_ollama(ollama_input)
    chat_mode = not ollama_input.startswith("*EVENT")

    if parameters.get("announce_status", False):
        printlog("Got Response:\n" + json.dumps(response, indent=4))

    ollama_response = response.get('message', {}).get('content', "No response")
    tokens_in_prompt   = response.get('prompt_eval_count', 0)
    tokens_in_response = response.get('eval_count',        0)
    total_s = response.get('total_duration', 0) / 1_000_000_000

    printlog(
        f"\nProcess complete\n"
        f" - total duration:     {total_s}\n"
        f" - tokens in prompt:   {tokens_in_prompt}\n"
        f" - tokens in response: {tokens_in_response}\n"
        f" - response:           {ollama_response.replace('\r\n', '\n').replace('\n', ' ')}"
    )

    if chat_mode:
        update_history(ollama_input, ollama_response)
    else:
        evt = ollama_input.replace("*EVENT", "").strip()
        update_history(evt, ollama_response)

    output_response(ollama_input, ollama_response)
    return True

MAX_WORDS = 200

def group_lines_by_speaker_and_chunk(new_lines, max_words=200):
    timestamped_pattern = re.compile(r'^\[\d{4}/\d{2}/\d{2}-\d{2}:\d{2}:\d{2}\]\s+(.*)$')

    cleaned_lines = []
    skipped = []

    for i, line in enumerate(new_lines):
        line = line.strip()
        if not line:
            continue

        match = timestamped_pattern.match(line)
        if match:
            content = match.group(1).strip()
        else:
            content = line  

        if len(content.split()) < 2 or len(content) < 5:
            skipped.append((i + 1, line))
        else:
            cleaned_lines.append(content)

    all_words = []
    line_accumulator = []
    final_chunks = []

    for line in cleaned_lines:
        words = line.split()
        if not words:
            continue

        if len(all_words) + len(words) > max_words:
            chunk = " ".join(line_accumulator).strip()
            if len(chunk.split()) >= 50:
                final_chunks.append(chunk)
            line_accumulator = []
            all_words = []

        line_accumulator.append(line)
        all_words.extend(words)

    if line_accumulator:
        chunk = " ".join(line_accumulator).strip()
        if len(chunk.split()) >= 50:
            final_chunks.append(chunk)

    return final_chunks, skipped


def add_to_player_chat_context():
    
    os.makedirs(PLAYER_CONTEXT_DIR, exist_ok=True)
    printlog("🧩 Running incremental FAISS chat update...")

    last_line_path = os.path.join(PLAYER_CONTEXT_DIR, "last_line_index.txt")
    last_index = 0
    if os.path.exists(last_line_path):
        with open(last_line_path, "r") as f:
            last_index = int(f.read().strip())

    if os.path.exists(PLAYER_FAISS_INDEX):
        index = faiss.read_index(PLAYER_FAISS_INDEX)
    else:
        index = faiss.IndexFlatL2(384)

    if os.path.exists(PLAYER_METADATA):
        with open(PLAYER_METADATA, "r", encoding="utf-8") as f:
            metadata = json.load(f)
    else:
        metadata = []

    with open(CHAT_LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
        all_lines = f.readlines()

    new_lines = all_lines[last_index:]
    printlog(f"📈 Lines in chatlog: {len(all_lines)} | New lines: {len(new_lines)}")

    grouped, skipped = group_lines_by_speaker_and_chunk(new_lines)

    printlog(f"🧠 Created {len(grouped)} new speaker chunks (incremental update)")

    if skipped:
        printlog(f"⚠️ Skipped {len(skipped)} malformed line(s):")
        for ln, content in skipped[:10]:
            printlog(f"  [Line {ln}] {content}")
        if len(skipped) > 10:
            printlog(f"  ... and {len(skipped) - 10} more")

    if not grouped:
        printlog("⚠️ No new lines to embed.")
        return

    printlog("🚀 Generating embeddings...")
    embeddings = EMBED_MODEL.encode(grouped, show_progress_bar=True)

    index.add(embeddings)
    for chunk in grouped:
        metadata.append({
            "chunk_id": len(metadata),
            "text": chunk
        })

    faiss.write_index(index, PLAYER_FAISS_INDEX)
    with open(PLAYER_METADATA, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    with open(last_line_path, "w") as f:
        f.write(str(len(all_lines)))

    printlog("✅ Incremental context update complete.")



def update_player_chat_context(bypass_flag=False):
    
    global last_chat_line_index
    printlog(f"📌 Writing to actual resolved paths:\n - index: {os.path.abspath(PLAYER_FAISS_INDEX)}\n - metadata: {os.path.abspath(PLAYER_METADATA)}")

    if not parameters.get("build_chat_context", False) and not bypass_flag:
        printlog("🚫 Skipping: build_chat_context is False.")
        return

    os.makedirs(PLAYER_CONTEXT_DIR, exist_ok=True)

    if os.path.exists(PLAYER_FAISS_INDEX):
        index = faiss.read_index(PLAYER_FAISS_INDEX)
    else:
        index = faiss.IndexFlatL2(384)

    if os.path.exists(PLAYER_METADATA):
        with open(PLAYER_METADATA, "r", encoding="utf-8") as f:
            metadata = json.load(f)
    else:
        metadata = []

    with open(CHAT_LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    new_lines = lines[last_chat_line_index:]
    printlog(f"📑 Total chat log lines: {len(lines)} | New lines: {len(new_lines)}")

    grouped, skipped = group_lines_by_speaker_and_chunk(new_lines)

    printlog(f"🧠 Created {len(grouped)} speaker chunks from full rebuild")

    if skipped:
        printlog(f"⚠️ Skipped {len(skipped)} malformed line(s):")
        for ln, content in skipped[:10]:
            printlog(f"  [Line {ln}] {content}")
        if len(skipped) > 10:
            printlog(f"  ... and {len(skipped) - 10} more")

    if grouped:
        embeddings = EMBED_MODEL.encode(grouped, show_progress_bar=True)
        index.add(embeddings)
        for chunk in grouped:
            metadata.append({
                "chunk_id": len(metadata),
                "text": chunk
            })

        faiss.write_index(index, PLAYER_FAISS_INDEX)
        with open(PLAYER_METADATA, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        printlog(f"📥 Added {len(grouped)} new player chat(s) to RAG index.")
    else:
        printlog("⚠️ No new chat chunks to add.")

    last_chat_line_index += len(new_lines)
    printlog(f"📈 Updated last_chat_line_index to: {last_chat_line_index}")

    printlog("✅ Player chat context update complete.")

def reload_player_chat_context():
    try:
        if os.path.exists(PLAYER_FAISS_INDEX) and os.path.exists(PLAYER_METADATA):
            faiss_index = faiss.read_index(PLAYER_FAISS_INDEX)
            with open(PLAYER_METADATA, "r", encoding="utf-8") as f:
                metadata = json.load(f)
            loaded_contexts["player_chats"] = (faiss_index, metadata)
            printlog("🔁 Reloaded 'player_chats' context.")
        else:
            printlog("⚠️ Player chat FAISS index or metadata not found. Skipping reload.")
    except Exception as e:
        printlog(f"❌ Failed to reload player chat context: {e}")


def start_background_chat_context_loop():
    def loop():
        while True:
            printlog("🧪 Chat context background loop running...")
            extract_parameters()
            printlog(f"📌 build_chat_context is: {parameters.get('build_chat_context', False)}")

            if parameters.get("build_chat_context", False):
                printlog("➡️ Calling add_to_player_chat_context()")
                add_to_player_chat_context()
                printlog("➡️ Calling reload_player_chat_context()")
                reload_player_chat_context()
            else:
                printlog("⛔ build_chat_context is false. Skipping context build.")

            time.sleep(parameters.get("build_chat_context_interval", 15))


    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
       
def initialize():
    global last_used_options, last_chat_line_index 

    printlog(f"{header_bar}\n{get_timestamp()}Process started.")

    extract_parameters(announce_params=True, compare_to_last_used_options=True, initialize=True)

    if parameters.get("force_rebuild_chat_context", False):
        last_chat_line_index = 0
        try:
            if os.path.exists(PLAYER_FAISS_INDEX):
                os.remove(PLAYER_FAISS_INDEX)
                printlog("🗑️ Deleted existing FAISS index for rebuild.")
            if os.path.exists(PLAYER_METADATA):
                os.remove(PLAYER_METADATA)
                printlog("🗑️ Deleted existing metadata for rebuild.")
        except Exception as e:
            printlog(f"❌ Error deleting context files: {e}")

    load_all_contexts()

    default_history = get_default_history()

    if parameters["clear_history_on_start"]:
        printlog("\nClearing history file.")
        history = default_history
        with open(HISTORY_FILE, 'w') as file:
            json.dump(history, file)
    else:
        history = extract_history()
        printlog(f'\nLoaded history from {HISTORY_FILE}. Number of items: {len(history)-len(default_history)}/{parameters["history_size"]}')


    with open(CHAT_LOG_NO_DATA, 'w') as file:
        pass  
        
    printlog(f"\n{header_bar}\n")

def main():
    last_offset = 0
    last_processed_time = time.time()
    last_wait_time = None

    initialize()
    if parameters.get("build_chat_context", False):
        add_to_player_chat_context()
    #start_background_chat_context_loop()

    while True:
        try:
            size = os.path.getsize(CHAT_LOG_NO_DATA)
        except OSError:
            size = 0

        if size < last_offset:
            last_offset = 0

        new_lines = []
        with open(CHAT_LOG_NO_DATA, 'r', encoding='utf-8', errors='ignore') as f:
            f.seek(last_offset)
            for raw in f:
                new_lines.append(raw.rstrip('\n'))
            last_offset = f.tell()

        for line in new_lines:

            if line.startswith("*==settingchange==*"):
                printlog(f"\n{header_bar}\n{get_timestamp()}Processing setting change:\n{line}")
                process_line(line)
                last_processed_time = time.time()
                continue
            
            extract_parameters()
            printlog(f"\n{header_bar}\n{get_timestamp()}Processing line:\n{line}")
            handled = process_line(line)
            if handled:
                last_processed_time = time.time()
            else:
                printlog(f"\n{get_timestamp()}Skipping line: {line}\n")
                printlog(f"{get_timestamp()}Uptime: {format_time(time.time() - start_time)}")
                printlog(header_bar)

        if last_processed_time is not None:
            wait = int(time.time() - last_processed_time)
            if wait != last_wait_time:
                sys.stdout.write(f"\rWaiting for input: {format_time(wait)}")
                sys.stdout.flush()
                last_wait_time = wait

        time.sleep(parameters["parse_file_speed"])


if __name__ == "__main__":
    main()

