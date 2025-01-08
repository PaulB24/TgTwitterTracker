from bs4 import BeautifulSoup
from typing import Optional

def _get_latest_follow_from_html(html: str) -> Optional[str]:
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        spans = soup.find_all('span', class_='css-1jxf684')
        counter = 0
        for span in spans:
            if span.text and span.text.strip().startswith('@'):
                counter += 1
                print(counter)
                if counter == 3:  # Return the third match
                    username_text = span.text.strip()
                    return username_text[1:] if username_text.startswith('@') else username_text
                        
            
    except Exception as e:
        print(f"Error parsing HTML for latest follow of: {str(e)}")
        return None

with open('test.html', 'r', encoding='utf-8') as file:
    html = file.read()

print(_get_latest_follow_from_html(html))