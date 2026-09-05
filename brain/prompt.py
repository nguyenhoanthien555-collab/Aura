from dataclasses import dataclass, field


@dataclass
class Prompt:

    sections: list[str] = field(default_factory=list)

    def add(self, *parts):

        for part in parts:
            if part:
                self.sections.append(part)

    def render(self):

        return "\n\n".join(self.sections)   