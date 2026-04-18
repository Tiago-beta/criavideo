import re

with open('static/index.html', 'r', encoding='utf-8') as f:
    lines = f.readlines()

depth = 0
wizard_depth = None

# Track from line 412 to 690
for i, line in enumerate(lines, 1):
    opens = re.findall(r'<div[\s>]', line)
    closes = re.findall(r'</div>', line)
    
    if i == 412:
        wizard_depth = depth
        print(f"L{i:4d} d={depth} WIZARD OPENS")
    
    for _ in opens:
        depth += 1
        if 412 <= i <= 690 and depth == wizard_depth + 2:
            # This is a direct child div opening
            id_match = re.search(r'id="([^"]*)"', line)
            cls_match = re.search(r'class="([^"]*)"', line)
            print(f"L{i:4d} d={depth} CHILD OPEN id={id_match.group(1) if id_match else '-'} class={cls_match.group(1) if cls_match else '-'}")
    
    for _ in closes:
        if 412 <= i <= 690 and depth == wizard_depth + 2:
            print(f"L{i:4d} d={depth} CHILD CLOSE")
        depth -= 1

print(f"\nAt line 690, depth={depth+len(re.findall(r'</div>', lines[689]))}, expected={wizard_depth+1} for wizard to be closed")
