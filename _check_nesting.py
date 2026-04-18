from html.parser import HTMLParser

class DivChecker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.in_modal = False
        self.modal_depth = 0
        self.ids_of_interest = {
            'modal-new-project', 'create-mode-selection',
            'create-panel-wizard', 'create-panel-script',
            'create-panel-library', 'create-panel-realistic'
        }

    def handle_starttag(self, tag, attrs):
        if tag in ('div', 'section', 'nav', 'main', 'button'):
            self.depth += 1
            attrs_dict = dict(attrs)
            id_val = attrs_dict.get('id', '')
            if id_val == 'modal-new-project':
                self.in_modal = True
                self.modal_depth = self.depth
                print(f'L{self.getpos()[0]:4d} depth={self.depth:2d} OPEN <div id="{id_val}">')
            elif self.in_modal and id_val in self.ids_of_interest:
                rel = self.depth - self.modal_depth
                print(f'L{self.getpos()[0]:4d} depth={self.depth:2d} rel={rel} OPEN <div id="{id_val}">')

    def handle_endtag(self, tag):
        if tag in ('div', 'section', 'nav', 'main', 'button'):
            if self.in_modal and self.depth == self.modal_depth:
                print(f'L{self.getpos()[0]:4d} depth={self.depth:2d} CLOSE </div> (modal-new-project)')
                self.in_modal = False
            self.depth -= 1

with open('static/index.html', 'r', encoding='utf-8') as f:
    p = DivChecker()
    p.feed(f.read())
