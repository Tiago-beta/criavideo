with open('static/index.html', 'r', encoding='utf-8') as f:
    lines = f.readlines()

depth = 0
# Find create-panel-wizard opening and track depth
wizard_start = None
wizard_depth = None

for i, line in enumerate(lines, 1):
    # Count div opens and closes
    import re
    opens = len(re.findall(r'<div[\s>]', line))
    closes = len(re.findall(r'</div>', line))
    
    old_depth = depth
    
    if 'create-panel-wizard' in line and '<div' in line:
        wizard_start = i
        wizard_depth = depth
        print(f"L{i:4d} depth={depth:2d} >> OPEN create-panel-wizard")
    
    depth += opens - closes
    
    if wizard_start and i >= wizard_start:
        if 'create-panel-script' in line or 'create-panel-library' in line or 'create-panel-realistic' in line:
            print(f"L{i:4d} depth={old_depth:2d}->{depth:2d} >> {line.strip()[:80]}")
        
        # Print when depth returns to wizard level (wizard closes)
        if depth <= wizard_depth and i > wizard_start:
            print(f"L{i:4d} depth={old_depth:2d}->{depth:2d} >> WIZARD PANEL CLOSES HERE")
            break
            
# Also check around lines 685-695
print("\n--- Lines 683-695 with depth tracking ---")
depth = 0
for i, line in enumerate(lines, 1):
    import re
    opens = len(re.findall(r'<div[\s>]', line))
    closes = len(re.findall(r'</div>', line))
    old_depth = depth
    depth += opens - closes
    if 683 <= i <= 695:
        print(f"L{i:4d} d={old_depth:2d}->{depth:2d} | {line.rstrip()}")
