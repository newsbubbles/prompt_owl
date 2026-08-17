# PrOwl: Prompt Owl - Give your prompts wings!
# Version 0.1 Origin: 2024-01-04
# Creator: Nathaniel Gibson @ LK Studio (github.com/lks-ai)
# Write prompt chains all in one file using this simple declarative language
# Augmented conditional prompt completion. Prompting *is* programming.
# Special thanks to @hattendo for his insights and helping me keep it minimal.

import re, os, asyncio
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
    PATTERN_FILL = r'\{([a-zA-Z_0-9]+)(?:\((\d+[^)]*)\))?\}'
    # Splits an argument list on commas that are not inside quotes
    PATTERN_ARGS = r'''(?:[^,"']|"[^"]*"|'[^']*')+'''
    # Regular expression for matching bullets or numbered list items
    PATTERN_LIST = r'^\s*(?:\*|\+|\-|\d+\.)\s+(.*)$'
    # Matches tool calls that trigger callbacks
    PATTERN_CALL = r"\{@(\w+)\((.*?)\)\}"
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
        def __init__(self, name:str=None, arg:tuple=None, value:str=None, list:list=None, data:dict=None, usage:VLLM.Usage=None):
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
    def parse_args(text, var_name=None):
        # (max_tokens, temperature) positionally, then key=value options. Values may be quoted.
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
        return re.sub(r'```prowl.*?```', lambda m: re.sub(r'[{}]', '\x00', m.group()), text, flags=re.DOTALL)

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
            usage.add(r['usage'])
        return completion, usage
    
    @staticmethod
    async def align_conditioning(
            prompt:str, 
            variable_attributes:tuple, 
            result_choice:dict, 
            usage:VLLM.Usage, 
            llm:VLLM, 
            multiline:bool, 
            continue_ratio=0.0,
            stops=None,
            retry_on_endswith=":",
            stream_level=StreamLevel.NONE,
            token_event=None,
        ):
        """Check the resulting correct and complete value"""
        # Insure that the LLM resulting generation
        variable_name, int_arg, float_arg = variable_attributes
        stops = stops or prowl.STOPS
        finish_reason = result_choice['finish_reason']
        completion:str = result_choice["text"].strip()
        
        async def ac(stop=None):
            # Helper function for automatic continuation on max_token length stop
            completion, use = await prowl.auto_continue(llm, prompt, result_choice["text"].strip(), variable_attributes, finish_reason, continue_ratio, stops=stop or stops, multiline=multiline, stream_level=stream_level, token_event=token_event)
            usage.add(use)
            return completion
        
        if not multiline: # make sure to trash multiline hallucinations
            completion = completion.strip(prowl.PATTERN_STRIP)
            #if it has a return character then we should retry...
            if "\n" in completion:
                # print("HAS A RETURN!")
                gvt = completion.split("\n", 2)
                gvi:str = gvt[0].strip(prowl.PATTERN_STRIP)
                if gvi.endswith(retry_on_endswith):
                    completion = ""
                completion = gvi
            else:
                if completion.endswith(retry_on_endswith):
                    # print("ERROR", completion)
                    completion = ""
                else:
                    completion = await ac(stop=["\n"])
            completion = completion.strip(prowl.PATTERN_STRIP)
            # print('FINAL', completion)
        else: # If multiline, just check autocontinue
            completion = prowl.strip_stops(completion, stops)
            completion = await ac()
            completion = prowl.strip_stops(completion, stops)
        return completion

    @staticmethod
    async def fill(template:str, stops:list[str]=None, variables:dict[str,Variable]=None, callbacks:dict=None, continue_ratio=0.0, stream_level=StreamLevel.NONE, stop_event=None, token_event=None, variable_event=None, script_name=None, silent:bool=False, model:str=None):
        if variables is None:
            variables = {}
        stops = stops or prowl.STOPS
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
            nnchar, nchar, schar = template[me:me+2], template[me:me+1], template[start_index-1:start_index]
            mult = nchar == "\n" and schar == "\n"
            multiline = nnchar == "\n\n" and mult
            
            prompt += text_segment

            if match.group(2) is not None:
                # Okay, first do a back-check to see if there are tool calls present somewhere before this variable
                if callbacks:
                    prompt, variables, stop = await prowl.run_callbacks(prompt, callbacks, variables, stream_level=stream_level, variable_event=variable_event, script_name=script_name)
                # It's a declaration, ask the LLM for a value
                int_arg, float_arg, opts = prowl.parse_args(match.group(2), var_name)
                # `block`/`inline` override what the surrounding whitespace implied
                if opts.pop('block', False):
                    multiline = True
                if opts.pop('inline', False):
                    multiline = False
                var_stops = opts.pop('stop', None) or stops
                # Loop the call until a valid generated value is present for that variable
                if not silent:
                    log.info(f"<< {var_name}({int_arg}, {float_arg}) >> multiline: {multiline}")
                completion = ""
                max_retries, tries = 4, 0
                fad = 1.0 - float_arg
                while completion == "":
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
                            **opts,
                        )
                        usage.add(r['usage'])
                    except APIError as e:
                        if e.fatal():
                            raise
                        tries += 1
                        if tries >= max_retries:
                            raise
                        log.warn(f"HTTP {e.status} on `{var_name}`, retry {tries}/{max_retries}")
                        await asyncio.sleep(4)
                        continue
                    except Exception as e:
                        tries += 1
                        if tries >= max_retries:
                            raise
                        log.warn(f"{type(e).__name__} on `{var_name}`: {e}, retry {tries}/{max_retries}")
                        await asyncio.sleep(4)
                        continue
                    # get the final completion and perform cleanup from 0th choice
                    completion = await prowl.align_conditioning(prompt, (var_name, int_arg, float_arg), r['choices'][0], usage, llm, multiline, continue_ratio=continue_ratio, stops=var_stops, stream_level=stream_level, token_event=token_event)
                    tries += 1
                    if tries >= max_retries:
                        left_context = None if len(prompt) < 30 else prompt[-30:].replace("\n", "\\n")
                        if not silent:
                            log.error(f"prompt was:\n{prompt}")
                        raise GenerationError(var_name, f"empty after {max_retries} attempts",
                            data={'context': left_context, 'script': script_name})
                if not silent:
                    log.info(completion)
                generated_list = prowl.extract_lists(completion)
                v = {'value': completion, 'usage': r['usage'], 'arg': (int_arg, float_arg)}
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

