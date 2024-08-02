import json
from bs4 import BeautifulSoup




# Function to extract text from blocks
def extract_text_from_blocks(blocks):
    return ' '.join(block['Text'] for block in blocks if 'Text' in block)


def convert_html_to_text(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')

    # Extract text and replace certain tags with new lines
    for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'p']):
        tag.insert_before('\n')
        tag.insert_after('\n')

    # Get the text
    text = soup.get_text()

    # Remove multiple newlines
    text = '\n'.join(line.strip() for line in text.splitlines() if line.strip())

    return text



def clean_and_convert(input_str):
    try:
        # Remove newlines and backslashes
        input_str = input_str.replace("\\n", "").replace("\\", "").strip()

        # Ensure the string is wrapped in an array
        if not input_str.startswith('['):
            input_str = '[' + input_str
        if not input_str.endswith(']'):
            input_str = input_str + ']'

        print(input_str)

        # Convert string to JSON
        feedback_list = json.loads(input_str)

        return feedback_list
    except json.JSONDecodeError as e:
        print(f"Failed to decode JSON: {e}")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None