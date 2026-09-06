#!/usr/bin/env python3
from pathlib import Path

P=Path('en_validation_project_dashboard_rc6.py')


def main():
    text=P.read_text(encoding='utf-8')
    imp='import ev_shadow_validation_view_rc6 as shadow_view\n'
    anchor='import bg_paper_dashboard as bg\n'
    if imp not in text:
        if anchor not in text: raise SystemExit('RC6_VALIDATION_SHADOW_IMPORT_ANCHOR')
        text=text.replace(anchor,anchor+imp,1)
    old='''        f"<div class='paper-grid'>{cards}</div>"\n        "<div class='paper-card'><h2>Hitos del camino crítico M0–M11</h2>"'''
    new='''        f"<div class='paper-grid'>{cards}</div>"\n        + shadow_view.render()\n        + "<div class='paper-card'><h2>Hitos del camino crítico M0–M11</h2>"'''
    if old in text:
        text=text.replace(old,new,1)
    elif 'shadow_view.render()' not in text:
        raise SystemExit('RC6_VALIDATION_SHADOW_BODY_ANCHOR')
    P.write_text(text,encoding='utf-8')
    print('RC6_VALIDATION_SHADOW_VIEW=GREEN')

if __name__=='__main__': main()
