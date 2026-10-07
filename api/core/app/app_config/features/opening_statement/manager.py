from typing import Any


OPENING_TEMPLATE_MAX_LENGTH = 50_000


class OpeningStatementConfigManager:
    @classmethod
    def convert_template(cls, config: dict[str, Any]) -> str | None:
        """
        Get the custom HTML/CSS template rendered below the opening statement.
        """
        return config.get("opening_template") or None
    @classmethod
    def convert(cls, config: dict[str, Any]) -> tuple[str, list[str]]:
        """
        Convert model config to model config

        :param config: model config args
        """
        # opening statement
        opening_statement = config.get("opening_statement", "")

        # suggested questions
        suggested_questions_list = config.get("suggested_questions", [])

        return opening_statement, suggested_questions_list

    @classmethod
    def validate_and_set_defaults(cls, config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """
        Validate and set defaults for opening statement feature

        :param config: app model config args
        """
        if not config.get("opening_statement"):
            config["opening_statement"] = ""

        if not isinstance(config["opening_statement"], str):
            raise ValueError("opening_statement must be of string type")

        if not config.get("opening_template"):
            config["opening_template"] = ""

        if not isinstance(config["opening_template"], str):
            raise ValueError("opening_template must be of string type")

        if len(config["opening_template"]) > OPENING_TEMPLATE_MAX_LENGTH:
            raise ValueError(f"opening_template must be at most {OPENING_TEMPLATE_MAX_LENGTH} characters")

        # suggested_questions
        if not config.get("suggested_questions"):
            config["suggested_questions"] = []

        if not isinstance(config["suggested_questions"], list):
            raise ValueError("suggested_questions must be of list type")

        for question in config["suggested_questions"]:
            if not isinstance(question, str):
                raise ValueError("Elements in suggested_questions list must be of string type")

        return config, ["opening_statement", "opening_template", "suggested_questions"]
