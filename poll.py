import re
from mautrix.types import (EventType, ReactionEvent, RedactionEvent)
from maubot import Plugin, MessageEvent
from maubot.handlers import command, event

import logging
log = logging.getLogger("maubot.client")

POLL_REGEX = r"\"((?:.|\n)*?)\"|^(.+)$"
EMOJI_REGEX = r"^(?:[\u2600-\u26FF\u2700-\u27BF\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F900-\U0001F9FF]|\d\ufe0f\u20e3)"
DEFAULT_REACTIONS = [f"{n}\ufe0f\u20e3" for n in range(1,10)]

class Choice:
    def __init__(self, text, emoji):
        self.text = text
        self.emoji = emoji
        self.count = 0

class Vote:
    def __init__(self, choice, user_id, event_id):
        self.choice = choice
        self.user_id = user_id
        self.event_id = event_id

class Poll:
    @classmethod
    def parse(cls, poll_setup, author=None):
        setup = re.findall(POLL_REGEX, poll_setup, re.MULTILINE)
        if len(setup) < 3:
            raise ValueError("Not enough arguments supplied (three at least for the question and two choices).")
        else:
            # Chose the correct capturing group since the regex has two capturing groups for the quote sign and newline syntax:
            question = setup[0][0] if setup[0][0] != '' else setup[0][1]
            choices = [choice[0] if choice[0] != '' else choice[1] for choice in setup[1:]]
            return cls(question, choices, author)

    def __init__(self, question, choices, author=None):
        self.question = question
        self.choices = []
        self.votes = []
        self.author = author

        emojis = []
        for i, choice in enumerate(choices):
            choice_trimmed = choice.strip()
            x = re.match(EMOJI_REGEX, choice)
            if x:
                emoji = choice_trimmed[:x.span()[1]]
                choice_trimmed = choice_trimmed[x.span()[1]:].strip()
                log.debug(f"Extracted emoji '{emoji}' for choice '{choice_trimmed}'")
            else:
                emoji = None
            choices[i] = choice_trimmed
            emojis.append(emoji)

        default_reactions_filtered = list(set(DEFAULT_REACTIONS).difference(set(emojis)))
        default_reactions_filtered.sort(reverse=True)

        # ToDo: what if more choices without user supplied emojis than default_reactions?
        for i, choice in enumerate(choices):
            self.choices.append(Choice(choice, emojis[i] if emojis[i] else default_reactions_filtered.pop()))

    def vote(self, reaction, user_id, event_id):
        log.debug(f"Vote for {reaction} from '{user_id}' (event ID: {event_id})")
        self.votes.append(Vote(self.get_choice(reaction), user_id, event_id))

    def unvote(self, redact_id):
        vote = self.get_vote_by_event(redact_id)
        if vote:
            log.debug(f"Vote withdrawn ({vote.choice.emoji} from '{vote.user_id}')")
            self.votes.remove(vote)

    def get_vote_by_event(self, event_id):
        for vote in self.votes:
            if vote.event_id == event_id:
                return vote
        return None

    def get_choice(self, reaction):
        for choice in self.choices:
            if choice.emoji == reaction:
                return choice
        return None

    def get_poll(self):
        choice_list = "  \n".join(
            [f"{choice.emoji}: {choice.text}" for choice in self.choices]
        )
        return f"""Poll created by {self.author} (ID: {self.index+1})

**{self.question}**

{choice_list}"""

    def count(self):
        # Rest choice count:
        for choice in self.choices:
            choice.count = 0
        # Count votes:
        voters = set()
        for vote in self.votes:
            vote.choice.count += 1
            voters.add(vote.user_id)
        self.voters_count= len(voters)

    def get_results(self):
        self.count()
        votes_count = len(self.votes)
        results = "  \n".join([f"{choice.emoji} {choice.count}/{self.voters_count} : {choice.text} " for choice in self.choices])
        results=f"""# Poll results
**{self.question}**

({self.voters_count} unique voters voted {votes_count} times)

{results}
"""
        return results

class PollBot(Plugin):
    currentPolls = {}

    async def create_poll(self, evt, poll_setup):
        try:
            poll = Poll.parse(poll_setup, evt.sender)
        except ValueError:
            response = """You need to enter at least 2 choices.
Syntax: '!poll "Question" "choice" "choice" ...'  
or: '!poll Question  
choice  
choice'

If the first character of a choice is an emoji it will be used for voting instead a default one."""
            await evt.reply(response)
            return None
        else:
            if evt.room_id not in self.currentPolls:
                self.currentPolls[evt.room_id] = []
            poll_index = len(self.currentPolls[evt.room_id])
            poll.index = poll_index
            self.currentPolls[evt.room_id].append(poll)
            return poll

    @command.new("poll", help='Creates a new poll. Usage \'!poll "Question" "choice" "choice" ... \'')
    @command.argument("poll_setup", pass_raw=True, required=True)
    async def poll_handler(self, evt: MessageEvent, poll_setup: str) -> None:
        poll = await self.create_poll(evt, poll_setup)
        if poll:
            response = poll.get_poll()
            poll.event_id = await evt.reply(response)
            for choice in poll.choices:
                await evt.client.react(evt.room_id, poll.event_id, choice.emoji)

    @command.new("lightpoll", help='Creates a poll directly from the message.')
    @command.argument("poll_setup", pass_raw=True, required=True)
    async def lightpoll_handler(self, evt: MessageEvent, poll_setup: str) -> None:
        poll = await self.create_poll(evt, poll_setup)
        if poll:
            poll.event_id = evt.event_id
            for choice in poll.choices:
                await evt.client.react(evt.room_id, poll.event_id, choice.emoji)

    @command.new("pollresults", help='Shows results for current poll.')
    @command.argument("poll_id", required=False)
    async def pollresults_handler(self, evt: MessageEvent, poll_id=None) -> None:
        await evt.mark_read()
        if evt.room_id in self.currentPolls:
            if poll_id == '':
                index = -1
            else:
                try:
                    poll_id = int(poll_id)
                except:
                    await evt.reply("Malformed ID not known.")
                    return
                else:
                    if poll_id > 0 and poll_id <= len(self.currentPolls[evt.room_id]):
                        index = poll_id - 1
                    else:
                        await evt.reply("Poll ID not known.")
                        return
            response = self.currentPolls[evt.room_id][index].get_results()
            await evt.reply(response)
        else:
            await evt.reply("No active polls in this room.")
            return

    @event.on(EventType.REACTION)
    async def get_react_vote(self, evt: ReactionEvent) -> None:
        if evt.sender != self.client.mxid:
            if evt.room_id in self.currentPolls:
                for poll in self.currentPolls[evt.room_id]:
                    if evt.content.relates_to.event_id == poll.event_id:
                        poll.vote(evt.content.relates_to.key, evt.sender, evt.event_id)
                        break

    @event.on(EventType.ROOM_REDACTION)
    async def get_redact_vote(self, evt: RedactionEvent) -> None:
        if evt.room_id in self.currentPolls:
            for poll in self.currentPolls[evt.room_id]:
                poll.unvote(evt.redacts)
