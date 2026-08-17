# PrOwl: Prompt Owl - Give your prompts wings!
# Version 0.1 Origin: 2024-01-04
# Creator: Nathaniel Gibson @ LK Studio (github.com/lks-ai)
# Write prompt chains all in one file using this simple declarative language
# Augmented conditional prompt completion. Prompting *is* programming.
# Special thanks to @hattendo for his insights and helping me keep it minimal.

import re, os, random, asyncio
from enum import Enum
from typing import Any
from .vllm import VLLM
from .tool import ProwlTool
from .log import log
from .error import APIError, ValidationError, GenerationError

class prowl:
    
    # Pattern to match both variable declarations and references.
    # The argument list is captured whole and parsed by parse_args, but it must still
    # START with an integer to count as a declaration. That is what keeps prose like
    # "use the syntax {variable_name(max_tokens, temperature)}" from becoming one.
    PATTERN_FILL = r'\{([a-zA-Z_0-9]+)(?::([a-zA-Z_0-9]+))?(?:\((\d[^)]*)\))?\}'
    # Splits an argument list on commas that are not inside quotes
    PATTERN_ARGS = r'''(?:[^,"']|"[^"]*"|'[^']*')+'''
    # Regular expression for matching bullets or numbered list items
    PATTERN_LIST = r'^\s*(?:\*|\+|\-|\d+\.)\s+(.*)$'
    # Matches tool calls that trigger callbacks
    PATTERN_CALL = r"\{@(\w+)\((.*?)\)\}"
    # A ```prowl block shows the language to the model, so its braces must not parse as variables
    PATTERN_MASK = r'```prowl.*?```'
    # Matches markdown randomness on single-line values for stripping
    PATTERN_STRIP = ' .-_*>#`\n'
    # One default for every entry point. A blank line, or a markdown header.
    # Bare `##` is here on purpose even though it also fires mid-line: models routinely run a
    # header onto the current line ("Since## Step 2"), which `\n#` cannot catch, and every
    # script in the library is markdown. Variables generating code or prose that contains `##`
    # override it per declaration with stop=.
    STOPS = ["\n\n", "\n#", "##"]
    # Temperature when a declaration gives max_tokens only
    TEMPERATURE = 0.0
    # Declaration options after (max_tokens, temperature). `block`/`inline` override the
    # whitespace shape heuristic, the rest go to the API. Unknown keys raise rather than
    # pass through: kwargs land straight in the request body, which is how `stops=` instead
    # of `stop=` silently disabled stop sequences on every auto-continuation.
    OPTIONS = ('stop', 'n', 'logprobs', 'model', 'block', 'inline')
    FLAGS = ('block', 'inline')

    # A declared type says what kind of value the model is expected to produce. It decides the
    # stop sequence, how the raw completion is read, and what counts as a value at all -- so
    # `max_tokens` goes back to being a runaway guard instead of doubling as a shape hint.
    #
    # A type annotates a declaration, it never makes one: the parentheses are what declares.
    # `{answer:number(8, 0.0)}` declares, `{answer}` references, and a reference has nothing to
    # type because it only splices a value that already exists.
    #
    # `bounded` types cannot contain a newline, so a newline is a true boundary and the server
    # can stop there. Running out of budget on one means it was cut off mid-value, which is the
    # failure that silently produced wrong answers for years: {answer(2, 0.0)} returned
    # "Let's denote" and {answer(8, 0.0)} returned "The final answer is: $\boxed{". Both raise now.
    #
    # Unbounded types have no safe delimiter -- any character can occur inside prose -- so they
    # stop at the next markdown header only, never at a blank line. A blank line inside prose is
    # not the end of the value; that default is what cut a chain-of-thought off after one sentence.
    TYPES = {
        'word':   {'stops': ['\n', ' '], 'bounded': True},
        'line':   {'stops': ['\n'],      'bounded': True},
        'number': {'stops': ['\n'],      'bounded': True},
        'bool':   {'stops': ['\n'],      'bounded': True},
        'text':   {'stops': ['\n#'],     'bounded': False},
        'list':   {'stops': ['\n#'],     'bounded': False},
    }
    TRUE = ('yes', 'true', 'y', '1', 'correct', 'affirmative')
    FALSE = ('no', 'false', 'n', '0', 'incorrect', 'negative')

    # Stream levels tell 
    class StreamLevel(Enum):
        TOKEN = 'token'
        VARIABLE = 'variable'
        SCRIPT = 'script'
        NONE = 'none'
    
    @staticmethod
    def load(path):
        try:
            with open(path, "r") as f:
                o = f.read()
            return o
        except Exception as e:
            pass
        return None

    class Variable:
        def __init__(self, name:str=None, arg:tuple=None, value:str=None, list:list=None, data:dict=None, usage:VLLM.Usage=None, type:str=None, truncated:bool=False):
            self.type = type
            self.truncated = truncated # the model was still going when the budget ran out
            self.name = name
            max_tokens, temperature = (None, None) if not arg else arg
            self.max_tokens = max_tokens
            self.temperature = temperature
            self.value = value
            self.list = list
            self.data = data
            self.new = True
            self.history = []
            self.usage:VLLM.Usage = VLLM.Usage()
            if usage:
                self.usage.add(usage)
            
        def push(self, variable):
            # Push self copy to history and replace properties with variable properties
            self.new = False
            self.history.append(self.to_dict())
            self.name, self.value, self.list, self.data = variable.name, variable.value, variable.list, variable.data
            self.type, self.truncated = variable.type, variable.truncated

        def first(self):
            return self if self.new else prowl.Variable(**self.history[0])
        
        def last(self):
            return self if self.new else prowl.Variable(**self.history[-1])
        
        def hist(self):
            h = self.history.copy()
            h.append(self.to_dict())
            return h

        def to_dict(self, history:bool=False, atomic=False) -> dict[str, Any]:
            d = {'value': self.value}
            if atomic:
                d['name'] = self.name
                d['arg'] = (self.max_tokens, self.temperature)
            if self.list:
                d['list'] = self.list
            if self.data:
                d['data'] = self.data
            if self.type:
                d['type'] = self.type
            if self.truncated:
                d['truncated'] = True
            if history:
                d['history'] = self.hist()
            if self.usage:
                d['usage'] = self.usage.dict()
            return d
        
        @staticmethod
        def from_dict(data):
            v = prowl.Variable(**data)
            return v

    @staticmethod
    def push_var(variables:dict[str, Variable], key:str, obj:dict):
        if key in variables:
            v:prowl.Variable = variables[key]
            v.push(prowl.Variable.from_dict(obj))
        else:
            obj['name'] = key
            variables[key] = prowl.Variable.from_dict(obj)
        return variables[key]
        
    class Return:
        def __init__(self, completion:str, variables:dict, usage:VLLM.Usage, output=None):
            self.completion:str = completion
            self.variables:dict[str, prowl.Variable] = variables
            self.usage:VLLM.Usage = usage
            self.output = output
            
        def val(self, key):
            if key in self.variables:
                return self.variables[key].value
            return None
        
        def var(self, key):
            if key in self.variables:
                v:prowl.Variable = self.variables[key]
                return v
            return None

        def data(self, key):
            if key in self.variables:
                if 'list' in self.variables[key]:
                    return self.variables[key]['list']
            return None
        
        def get(self):
            return {k: v.value for k, v in self.variables.items()}
        
        def out(self, index:int=0):
            if index:
                return self.output[index]['output']
            else:
                a = []
                for ou in self.output:
                    a.append(ou['output'])
                return "\n".join(a)
        
        def to_dict(self):
            vars = {k: v.to_dict(history=True) for k, v in self.variables.items()}
            return {
                'completion': self.completion,
                'variables': vars,
                'usage': self.usage.dict(),
                'output': self.output,
            }

    @staticmethod
    def backoff(tries, error=None):
        # honour Retry-After when the server sends one, else exponential. Jitter matters:
        # a stack fanned out over one provider retries in lockstep without it.
        after = (error.data or {}).get('retry_after') if isinstance(getattr(error, 'data', None), dict) else None
        if after:
            try:
                return min(float(after), 30.0)
            except ValueError:
                pass
        return min(2 ** tries, 30) * (0.5 + random.random() / 2)

    @staticmethod
    def parse_args(text, var_name=None, var_type=None):
        # (max_tokens, temperature) positionally, then key=value options. Values may be quoted.
        # A declared type carries its own budget, so `{answer:number}` needs no arguments at all.
        if var_type is not None and var_type not in prowl.TYPES:
            raise ValidationError(1008, f"`{var_name}` has unknown type `{var_type}`",
                data={'variable': var_name, 'type': var_type, 'known': list(prowl.TYPES)})
        pos, opts = [], {}
        for part in re.findall(prowl.PATTERN_ARGS, text):
            part = part.strip()
            if not part:
                continue
            key, eq, value = part.partition('=')
            key = key.strip()
            if not eq:
                if key in prowl.FLAGS:
                    opts[key] = True
                else:
                    pos.append(part.strip('"\''))
                continue
            if key not in prowl.OPTIONS:
                raise ValidationError(1005, f"Unknown option `{key}` on `{var_name}`",
                    data={'variable': var_name, 'option': key, 'known': list(prowl.OPTIONS)})
            opts[key] = value.strip().strip('"\'')

        if not pos:
            raise ValidationError(1006, f"`{var_name}` declares no max_tokens",
                data={'variable': var_name, 'args': text})
        if len(pos) > 2:
            # almost always an unquoted multi-value option: stop=.,\n splits on the comma and
            # leaves `\n` stranded here. Dropping it silently is the bug this class of check exists
            # to prevent, so say so and name the fix.
            raise ValidationError(1007,
                f"`{var_name}` has stray argument(s) {pos[2:]}: quote multi-value options, e.g. stop=\".,\\n\"",
                data={'variable': var_name, 'args': text, 'stray': pos[2:]})
        try:
            max_tokens = int(pos[0])
            temperature = float(pos[1]) if len(pos) > 1 else prowl.TEMPERATURE
        except ValueError:
            raise ValidationError(1006, f"`{var_name}` has non-numeric (max_tokens, temperature): {text}",
                data={'variable': var_name, 'args': text})

        if 'stop' in opts:
            opts['stop'] = [s.encode().decode('unicode_escape') for s in opts['stop'].split(',')]
        for k in ('n', 'logprobs'):
            if k in opts:
                opts[k] = int(opts[k])
        return max_tokens, temperature, opts

    @staticmethod
    def shape(template, start, end):
        # Inline or block, decided by the three characters around the declaration and nothing else:
        # alone on its line, and followed by a blank one. `fill` appends a newline before walking,
        # which is why a variable on the last line still counts as block.
        #
        # Named because it is the rule most often broken by accident -- a missing blank line
        # truncates a 1024-token narrative to its first line, invisibly -- so anything that shows
        # a script to a human should be able to show this, and agree with fill() when it does.
        mult = template[end:end + 1] == "\n" and template[start - 1:start] == "\n"
        return 'block' if (template[end:end + 2] == "\n\n" and mult) else 'inline'

    @staticmethod
    def extract_lists(text):
        pattern = prowl.PATTERN_LIST
        
        # Find all matches in the text
        matches = re.findall(pattern, text, re.MULTILINE)
        
        # Return the list of extracted items, stripped of leading/trailing spaces
        return None if not matches else [match.strip() for match in matches]

    @staticmethod
    async def run_callbacks(text:str, callbacks:dict[str, ProwlTool], variables:dict[str, Variable], stream_level=StreamLevel.NONE, variable_event=None, script_name=None):
        matches = re.finditer(prowl.PATTERN_CALL, text)

        # Parsing the results
        parsed_calls = []
        final_text = ""
        head, tail = 0, 0
        stop = False
        for match in matches:
            callback_text = match.group()
            callback_name = match.group(1) #match[0]
            # print('@>>', callback_name)
            # if variables:
            #     print(variables.keys())
            #print(callbacks)
            arguments = match.group(2).split(',') #match[1].split(',')
            head = match.start()
            text_segment = text[tail:head]
            final_text += text_segment
            # Stripping any leading/trailing whitespaces from the arguments
            arguments = [arg.strip() for arg in arguments]
            parsed_calls.append((callback_name, arguments, head, tail))

            # Run the parsed calls 
            if callback_name in callbacks:
                callback = callbacks[callback_name]
                marg, mkwarg = callback.map_args(arguments)
                vs = {k: v.value for k, v in variables.items()}
                vs.update(callback.ns_update())
                margs = [int(arg) if arg.isdigit() else vs.get(arg) for arg in marg]
                mkwargs = {k: int(v) if v.isdigit() else vs.get(v) for k, v in mkwarg.items()}
                # Fill [context level] variables into kwargs for recall within the tool using special methods
                mkwargs['__arguments'] = arguments
                mkwargs['__completion'] = text
                mkwargs['__variables'] = variables
                mkwargs['__stream_level'] = stream_level
                mkwargs['__call_match'] = callback_text
                result:ProwlTool.Return = await callback.run(*margs, **mkwargs)
            else: # tool is missing!
                raise ValueError(f"The tool `{callback_name}` is missing or not loaded in your callbacks argument")
            final_text += result.completion or ""
            variable:prowl.Variable = prowl.push_var(variables, callback_name, {'value': result.completion, 'data': result.data})
            if variable_event and stream_level.value == prowl.StreamLevel.VARIABLE.value:
                er = await variable_event(script_name, variable)
                if er is not None and er == False: # allow stopping on the variable event if it returns False
                    stop = True
            tail = match.end()
        final_text += text[tail:]
        return final_text, variables, stop
    
    @staticmethod
    def mask_prowl_code_blocks(text):
        # Neutralize the braces inside ```prowl blocks so they don't parse as variables.
        # The mask is the same length as the original, so match offsets stay valid
        # against the unmasked text that fill() slices.
        return re.sub(prowl.PATTERN_MASK, lambda m: re.sub(r'[{}]', '\x00', m.group()), text, flags=re.DOTALL)

    @staticmethod
    def strip_stops(value, stops):
        # Strip out anything that comes after a stop if that's the case and see if we get an empty
        stp = "|".join(stops)
        spl = re.split(stp, value)
        if len(spl) > 1:
            value = spl[0].strip()
        return value
    
    @staticmethod
    async def auto_continue(llm:VLLM, prompt:str, completion:str, var_attr:tuple, finish_reason:str, continue_ratio:float=0.5, stops=None, multiline=True, stream_level=StreamLevel.NONE, token_event=None):
        # Automatic continuation on max_token length stop
        variable_name, int_arg, float_arg = var_attr
        stops = stops or prowl.STOPS
        usage = VLLM.Usage()
        if finish_reason == 'length' and continue_ratio > 0.0 and int_arg > 1:
            # If we have stopped because of length, continue with some portion of max tokens
            extra_tokens = int(float(int_arg) * continue_ratio)
            # further generate with prompt + generated_value
            final_value = completion
            # print(f'...>> CONTINUING for {extra_tokens} tokens...')
            r = await llm.run_async(
                prompt + completion,
                max_tokens=extra_tokens,
                temperature=float_arg,
                stop=stops,
                streaming = stream_level == prowl.StreamLevel.TOKEN,
                stream_callback = token_event,
                variable_name=variable_name,
            )
            completion = final_value + r['choices'][0]['text']
            if not multiline:
                completion = prowl.strip_stops(completion, stops)
            usage.add(r.get('usage') or {})
        return completion, usage
    
    @staticmethod
    def read(var_type, text):
        """Read a raw completion as the declared type: (value, ok).

        ok=False means nothing usable for this type came back, which is a retry rather than a
        value. The old test was `completion == ""`, so anything surviving cleanup was accepted
        no matter what it held."""
        text = text or ""
        if var_type == 'number':
            # the LAST number: models answer "The final answer is: $\boxed{58}$"
            found = re.findall(r'-?\d+(?:\.\d+)?', text)
            return (found[-1] if found else ""), bool(found)
        if var_type == 'bool':
            head = text.strip().lower().split()
            word = re.sub(r'[^a-z0-9]', '', head[0]) if head else ""
            if word in prowl.TRUE:
                return 'true', True
            if word in prowl.FALSE:
                return 'false', True
            return text.strip(), False
        if var_type == 'word':
            words = text.strip(prowl.PATTERN_STRIP).split()
            return (words[0] if words else ""), bool(words)
        if var_type == 'list':
            value = text.strip()
            return value, bool(prowl.extract_lists(value))
        if var_type == 'text':
            value = text.strip()
            return value, bool(value)
        # 'line', and untyped inline variables
        value = text.strip(prowl.PATTERN_STRIP).split("\n")[0].strip(prowl.PATTERN_STRIP)
        # a line ending in a colon is the model announcing a list it never wrote
        return value, bool(value) and not value.endswith(":")

    @staticmethod
    async def resolve(llm, prompt, choice, var_type, var_attr, usage, stops, continue_ratio=0.0,
                      multiline=False, stream_level=StreamLevel.NONE, token_event=None):
        """Turn a raw completion into a value: (value, ok, truncated).

        `truncated` is whether the model was still going when the budget ran out. It used to be
        thrown away, which is why a value cut off mid-word was indistinguishable from a finished
        one."""
        truncated = choice.get('finish_reason') == 'length'
        text = choice.get('text') or ""
        bounded = prowl.TYPES.get(var_type, {}).get('bounded', not multiline)
        # only continue what is allowed to run long. A bounded value that overran is a defect to
        # report, not something to go and fetch more of.
        if truncated and continue_ratio > 0.0 and not bounded:
            text, use = await prowl.auto_continue(llm, prompt, text.strip(), var_attr, 'length',
                continue_ratio, stops=stops, multiline=True,
                stream_level=stream_level, token_event=token_event)
            usage.add(use)
        value, ok = prowl.read(var_type or ('text' if multiline else 'line'),
                               prowl.strip_stops(text.strip(), stops))
        return value, ok, truncated

    @staticmethod
    async def fill(template:str, stops:list[str]=None, variables:dict[str,Variable]=None, callbacks:dict=None, continue_ratio=0.0, stream_level=StreamLevel.NONE, stop_event=None, token_event=None, variable_event=None, script_name=None, silent:bool=False, model:str=None, extra:dict=None):
        if variables is None:
            variables = {}
        stops = stops or prowl.STOPS
        # run-level request fields the backend understands and prowl does not need to:
        # provider pinning, seed, response_format. Declaration options win over these.
        extra = extra or {}
        # callbacks are dict with 'var_name' as key and function as value
        # TODO add kwarg stop_condition is a dict with {'var_name': match_regex}
        # -> once implemented it will stop and return current results
        # ->   if the generated value re.match(match_regex)
        # -> is good for situations where a known value might be returned but it is invalid
        # -> perhaps it can be a callback as well, that just checks...
        # TODO Make a map for variable value rewrites
        # -> If you are expecting a return value that means a default, and you know the llm will return it sometimes
        # -> Map that generated value to a specified value with this map
        template += "\n" # dirty trick: to make any hanging output multiline :(
        # get matches on the masked template, but slice the real one: the mask preserves offsets
        pattern = re.compile(prowl.PATTERN_FILL)
        matches = list(pattern.finditer(prowl.mask_prowl_code_blocks(template)))
        prompt = ""
        last_index = 0
        llm = VLLM(
            f"{os.getenv('PROWL_VLLM_ENDPOINT')}",
            model=model or os.getenv('PROWL_MODEL'),
        )
        # accumulate token usage here
        usage = VLLM.Usage()
        # Iterate through variable declarations and references aggregating through the prompt
        stop = False
        for match in matches:
            # check for stop first
            if stop_event:
                stop = await stop_event()
            if stop:
                return prowl.Return(prompt, variables, usage)
            var_name = match.group(1)
            start_index = match.start()
            text_segment = template[last_index:start_index]
            me = match.end()
            multiline = prowl.shape(template, start_index, me) == 'block'

            prompt += text_segment

            var_type = match.group(2)
            # The parentheses are what declares. A type is an annotation on a declaration, never
            # a declaration on its own: a reference only splices a stored value, so it has
            # nothing to type.
            if match.group(3) is not None:
                # Okay, first do a back-check to see if there are tool calls present somewhere before this variable
                if callbacks:
                    prompt, variables, stop = await prowl.run_callbacks(prompt, callbacks, variables, stream_level=stream_level, variable_event=variable_event, script_name=script_name)
                # It's a declaration, ask the LLM for a value
                int_arg, float_arg, opts = prowl.parse_args(match.group(3) or '', var_name, var_type)
                # `block`/`inline` override what the surrounding whitespace implied
                if opts.pop('block', False):
                    multiline = True
                if opts.pop('inline', False):
                    multiline = False
                # a declared type brings its own boundary; an explicit stop= still wins
                var_stops = opts.pop('stop', None) or (
                    prowl.TYPES[var_type]['stops'] if var_type else stops)
                # Loop the call until a VALID value is present, not merely a non-empty one
                if not silent:
                    label = f"{var_name}:{var_type}" if var_type else var_name
                    log.info(f"<< {label}({int_arg}, {float_arg}) >> multiline: {multiline}")
                completion, ok, truncated = "", False, False
                max_retries, tries = 4, 0
                fad = 1.0 - float_arg
                while True:
                    fex = fad * (tries / max_retries)
                    try:
                        r = await llm.run_async(
                            prompt.rstrip(" "),
                            max_tokens = int_arg,
                            temperature = float_arg + fex,
                            stop = var_stops,
                            streaming = stream_level == prowl.StreamLevel.TOKEN,
                            stream_callback = token_event,
                            variable_name=var_name,
                            **{**extra, **opts},
                        )
                        usage.add(r.get('usage') or {})
                    except APIError as e:
                        if e.fatal():
                            raise
                        tries += 1
                        if tries >= max_retries:
                            raise
                        wait = prowl.backoff(tries, e)
                        log.warn(f"HTTP {e.status} on `{var_name}`, retry {tries}/{max_retries} in {wait:.1f}s")
                        await asyncio.sleep(wait)
                        continue
                    except Exception as e:
                        tries += 1
                        if tries >= max_retries:
                            raise
                        log.warn(f"{type(e).__name__} on `{var_name}`: {e}, retry {tries}/{max_retries}")
                        await asyncio.sleep(4)
                        continue
                    completion, ok, truncated = await prowl.resolve(
                        llm, prompt, r['choices'][0], var_type, (var_name, int_arg, float_arg),
                        usage, var_stops, continue_ratio=continue_ratio, multiline=multiline,
                        stream_level=stream_level, token_event=token_event)
                    # Validity is the test, not truncation. A truncated completion that still
                    # yields a good value gives us what was asked for; the rest was going to be
                    # discarded anyway. Truncation only explains a failure, it isn't one.
                    if ok:
                        break
                    tries += 1
                    if tries >= max_retries:
                        why = (f"no {var_type or 'value'} in the completion, cut off at "
                               f"max_tokens={int_arg}" if truncated
                               else f"no {var_type or 'value'} in the completion")
                        if not silent:
                            log.error(f"`{var_name}`: {why}; last value {completion!r}")
                        raise GenerationError(var_name, f"{why} after {max_retries} attempts",
                            data={'type': var_type, 'truncated': truncated,
                                  'value': completion, 'script': script_name})
                if truncated and not silent:
                    log.warn(f"`{var_name}` hit its {int_arg} token budget; the value is incomplete")
                if not silent:
                    log.info(completion)
                generated_list = prowl.extract_lists(completion)
                v = {'value': completion, 'usage': r.get('usage') or {},
                     'arg': (int_arg, float_arg), 'type': var_type, 'truncated': truncated}
                if generated_list:
                    v['list'] = generated_list
                if len(r['choices']) > 1: # n>1: keep the alternatives, don't bill for them and drop them
                    v['data'] = {'candidates': [c['text'].strip() for c in r['choices']]}
                variable:prowl.Variable = prowl.push_var(variables, var_name, v)
                prompt += completion
                # TODO add this variable_event to the tool callback so that tool variables also return
                #print(variable_event, stream_level.value == prowl.StreamLevel.VARIABLE.value, prowl.StreamLevel.VARIABLE, stream_level)
                if variable_event and stream_level.value == prowl.StreamLevel.VARIABLE.value:
                    #print(variable.to_dict())
                    await variable_event(script_name, variable)
            else:
                # It's a reference
                if var_type is not None:
                    raise ValidationError(1009,
                        f"`{var_name}:{var_type}` is a reference with a type: give it "
                        f"(max_tokens, temperature) to declare it, or drop the type to reference it",
                        data={'variable': var_name, 'type': var_type, 'script': script_name})
                if var_name in variables:
                    # Replace the reference with the stored value
                    var:prowl.Variable = variables[var_name]
                    prompt += f"{var.value}"
                else:
                    # Leave the reference as-is for now
                    prompt += f'{{{var_name}}}'

            last_index = match.end()

        # Add remaining text after the last match and check it's tool callbacks one last time
        # -> Tool check is for using tools at the end of a script
        prompt += template[last_index:]
        prompt, variables, stop = await prowl.run_callbacks(prompt, callbacks, variables, stream_level=stream_level, variable_event=variable_event, script_name=script_name)
        
        return prowl.Return(prompt, variables, usage)

