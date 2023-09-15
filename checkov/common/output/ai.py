from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

import openai
from termcolor import colored

from checkov.common.bridgecrew.check_type import CheckType

if TYPE_CHECKING:
    from checkov.common.output.record import Record
    from typing_extensions import Self

# Common OpenAI environment variables
OPENAI_MAX_FINDINGS = int(os.getenv("CKV_OPENAI_MAX_FINDINGS", 5))
OPENAI_MAX_TOKENS = int(os.getenv("CKV_OPENAI_MAX_TOKENS", 512))
OPENAI_MODEL = os.getenv("CKV_OPENAI_MODEL", "gpt-3.5-turbo")

RUNNER_DENY_LIST = {
    CheckType.POLICY_3D,
    CheckType.SCA_IMAGE,
    CheckType.SCA_PACKAGE,
    CheckType.SECRETS,
}


class OpenAi:
    _instance = None  # noqa: CCE003  # singleton

    def _validate_azure_env(self, value: str | None = None, environment_variable_name: str | None = None) -> bool:
        if (value is None):
            print(
                colored(
                    f"ERROR: Configuration for Azure OpenAI is missing: Please specify {environment_variable_name} environment variable for --openai-api-type '{self._api_type}' type.",
                    "red",
                )
            )
            return False
        return True

    def __new__(cls, api_key: str | None = None, api_type: str = "default") -> Self:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._should_run = True if api_key else False
            cls._api_type = api_type.lower()
            if (cls._api_type == 'azure'):
                cls.AZURE_OPENAI_API_ENDPOINT = os.getenv("CKV_AZURE_OPENAI_API_ENDPOINT", None)
                cls.AZURE_OPENAI_API_VERSION = os.getenv("CKV_AZURE_OPENAI_API_VERSION", '2023-05-15')
                cls.AZURE_OPENAI_DEPLOYMENT_NAME = os.getenv("CKV_AZURE_OPENAI_DEPLOYMENT_NAME", None)
                cls._should_run = cls._should_run & cls._validate_azure_env(cls._instance, cls.AZURE_OPENAI_API_ENDPOINT, 'CKV_AZURE_OPENAI_API_ENDPOINT')
                cls._should_run = cls._should_run & cls._validate_azure_env(cls._instance, cls.AZURE_OPENAI_API_VERSION, 'CKV_AZURE_OPENAI_API_VERSION')
                cls._should_run = cls._should_run & cls._validate_azure_env(cls._instance, cls.AZURE_OPENAI_DEPLOYMENT_NAME, 'CKV_AZURE_OPENAI_DEPLOYMENT_NAME')
                openai.api_type = cls._api_type
                openai.api_base = cls.AZURE_OPENAI_API_ENDPOINT if cls.AZURE_OPENAI_API_ENDPOINT is not None else ""
                openai.api_version = cls.AZURE_OPENAI_API_VERSION
            openai.api_key = api_key

        return cls._instance

    def enhance_records(self, runner_type: str, records: list[Record]) -> None:
        if not self._should_run:
            return
        if runner_type in RUNNER_DENY_LIST:
            return

        asyncio.run(self._generate_guidelines(records=records))

    async def _generate_guidelines(self, records: list[Record]) -> None:
        enhance_records = self._prioritize_findings(records=records)

        batches = [enhance_records]
        if len(enhance_records) > 20:
            # https://platform.openai.com/docs/guides/rate-limits/what-are-the-rate-limits-for-our-api
            # for free users 20 RPM is the limit, therefore splitting into batches of 10
            batch_size = 10
            batches = [records[i : i + batch_size] for i in range(0, len(enhance_records), batch_size)]

        self._print_warning(
            records_count=len(records),
            enhance_records_count=len(enhance_records),
            batches_count=len(batches),
        )

        for batch in batches:
            await asyncio.gather(*[self._chat_complete(record=record) for record in batch])

    async def _chat_complete(self, record: Record) -> None:
        if not record.code_block:
            # no need to ask OpenAI about guidelines, if we have no code blocks
            return

        try:
            # define common messages array
            messages = [
                {"role": "system", "content": "You are a security tool"},
                {
                    "role": "user",
                    "content": "".join(
                        [
                            f"fix following code, which violates checkov policy '{record.check_name}':\n",
                            *[line for _, line in record.code_block],
                        ]
                    ),
                },
                {"role": "user", "content": "Explain"},
            ],
            # depends on api_type, call ChatCompletion differently
            logging.info(f"[_chat_complete]: self._api_type: {self._api_type}")
            if (self._api_type == 'azure'):
                completion = await openai.ChatCompletion.acreate(  # type:ignore[no-untyped-call]
                    engine=self.AZURE_OPENAI_DEPLOYMENT_NAME,
                    messages=messages[0],
                    temperature=0,
                    max_tokens=OPENAI_MAX_TOKENS,
                )
            else:
                completion = await openai.ChatCompletion.acreate(  # type:ignore[no-untyped-call]
                    model=OPENAI_MODEL,
                    messages=messages[0],
                    temperature=0,
                    max_tokens=OPENAI_MAX_TOKENS,
                )

            logging.info(f"[COMPLETION]{completion}")
            logging.info(f"OpenAI request consumed {completion.usage.total_tokens} tokens")

            details = self._parse_completion_response(completion_content=completion.choices[0].message.content)
            if details:
                record.details = details
        except Exception:
            logging.info("Something went wrong while querying OpenAI", exc_info=True)

    def _prioritize_findings(self, records: list[Record]) -> list[Record]:
        if 0 < OPENAI_MAX_FINDINGS < len(records):
            # the higher severities should be preferred
            sorted_records = sorted(
                records, key=lambda record: record.severity.level if record.severity else 0, reverse=True  # type:ignore[has-type]
            )

            # to protect the user, just take the last x findings
            return sorted_records[-OPENAI_MAX_FINDINGS:]

        return records

    def _parse_completion_response(self, completion_content: str) -> list[str]:
        result = []

        if completion_content:
            result.append("The following text is AI generated and should be treated as a suggestion.")
            result.append("")

        in_code_block = False
        for line in completion_content.splitlines():
            if "```" in line:
                if in_code_block:
                    in_code_block = False
                else:
                    in_code_block = True
                continue
            if in_code_block:
                result.append(line)
            elif not line:
                result.append(line)
            else:
                result.extend(
                    sentence if sentence.endswith((".", ":")) else f"{sentence}."
                    for sentence in line.strip().split(". ")
                )

        return result

    def _print_warning(self, records_count: int, enhance_records_count: int, batches_count: int) -> None:
        max_findings_note = ""
        if 0 < OPENAI_MAX_FINDINGS < records_count:
            max_findings_note = (
                f"Found {records_count} failed checks and will provide enhanced guidelines for {OPENAI_MAX_FINDINGS}. To add enhanced guidelines for more findings,\n"
                "please adjust the env var 'CKV_OPENAI_MAX_FINDINGS' accordingly or set 0 to enhance all.\n"
            )

        print(
            colored(
                f"WARNING: About to request {enhance_records_count} enhanced guidelines and it may take up to {batches_count * 15}s.\n{max_findings_note}",
                "yellow",
            )
        )
