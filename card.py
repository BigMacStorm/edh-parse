class Card:
    def __init__(self, card_id: str, is_commander=False):
        self.commander = is_commander
        self.card_id = card_id