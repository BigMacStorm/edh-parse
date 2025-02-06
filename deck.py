from card import Card

class Deck:
    def __init__(self, commander_id: str, mainboard_ids):
        self.mainboard = []
        self.commander = Card(commander_id, is_commander=True)
        for card_id in mainboard_ids:
            self.mainboard.append(Card(card_id))
    
    #TODO: This will make an API call for each card grabbing needed information.
    def lookup_card_data(self):
        pass

    #TODO: This method will take in manabox sheet and filter out cards that are already owned.
    def mark_cards_as_owned(self):
        pass

    def generate_csv(self):
        pass

    def write_to_file(self):
        pass

    def read_from_file(self):
        pass