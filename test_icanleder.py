import icalendar

with open('assets/2024-2025-msar-public-holidays-zh-hans.ics', 'rb') as f:
    calendar_data = f.read()

calendar = icalendar.Calendar.from_ical(calendar_data)

with open('output.txt', 'w', encoding='utf-8') as output_file:
    for component in calendar.walk():
        if component.name == 'VEVENT':
            summary = component.get('summary')
            dtstart = component.get('dtstart').dt
            dtend = component.get('dtend').dt
            location = component.get('location')
            
            output_file.write(f"Summary: {summary}\n")
            output_file.write(f"Start: {dtstart}\n")
            output_file.write(f"End: {dtend}\n")
            output_file.write(f"Location: {location}\n\n")
