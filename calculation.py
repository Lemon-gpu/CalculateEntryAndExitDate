import pandas as pd
import pdfplumber
import re
from icalendar import Calendar
import json

holidays_path: str = 'assets/2024-2025-msar-public-holidays-zh-hans.ics'
holidays_calendar = Calendar().from_ical(open(holidays_path, 'r', encoding='utf-8').read())
winter_holiday: json = json.load(open('assets/Winter_holiday.json', 'r', encoding='utf-8'))
course: json = json.load(open('assets/Course.json', 'r', encoding='utf-8'))
today: pd.Timestamp = pd.Timestamp.now()

def is_workday(date: pd.Timestamp) -> bool:
    '''
    判断是否是工作日

    Parameters:
    date: pd.Timestamp, 日期
    '''

    # 如果是周末，那么一定不是工作日
    if date.dayofweek >= 5:
        return False
    
    # 如果是节假日，那么一定不是工作日
    for component in holidays_calendar.walk():
        if component.name == 'VEVENT':
            holiday_date: pd.Timestamp = pd.Timestamp(component.get('dtstart').dt)
            if date.date() == holiday_date.date():
                return False
            
    # 如果是寒假，那么一定不是工作日
    for description in winter_holiday:
        date_range: str = winter_holiday[description].split('-')
        start_date: pd.Timestamp = pd.Timestamp(date_range[0]).normalize()
        end_date: pd.Timestamp = pd.Timestamp(date_range[1]).normalize()
        if date.normalize() >= start_date and date.normalize() <= end_date:
            return False
        
    return True

def is_course(date: pd.Timestamp) -> bool:
    '''
    判断是否是上课时间

    Parameters:
    date: pd.Timestamp, 日期
    '''

    # 如果是周末，那么一定不是上课时间
    if date.dayofweek >= 5:
        return False
    
    # 如果是上课时间，那么一定是上课时间
    for description in course:
        current_semester: json = course[description]
        for period in current_semester:
            date_range: str = current_semester[period].split('-')
            start_date: pd.Timestamp = pd.Timestamp(date_range[0]).normalize()
            end_date: pd.Timestamp = pd.Timestamp(date_range[1]).normalize()
            if date.normalize() >= start_date and date.normalize() <= end_date:
                return True
            
    return False


def extract_tables_from_pdf(file_path: str, start_from: pd.Timestamp = None, end_at: pd.Timestamp = None) -> pd.DataFrame:
    table: pd.DataFrame = pd.DataFrame()
    first_table: bool = True
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            extracted_tables: list = page.extract_tables()
            for extracted_table in extracted_tables:
                if first_table:
                    table = pd.DataFrame(extracted_table[1:], columns=extracted_table[0])
                    first_table = False
                else:
                    table = pd.concat([table, pd.DataFrame(extracted_table, columns=table.columns)], ignore_index=True)
        
    table.drop(columns=["证件名称", "证件号码", "出入境口岸", "航班号"], inplace=True)
    # 把出入境日期转换为日期格式
    table["出入境日期"] = pd.to_datetime(table['出入境日期'], format='%Y-%m-%d')
    # 把序号转换为整数
    table["序号"] = table["序号"].astype(int)
    # 根据序号排序，要升序排列，这样保证是从晚到早
    table.sort_values(by='序号', ascending=True, inplace=True)

    # 如果start_from为None, 则取第一天；如果end_at为None, 则取最后一天
    if start_from is None:
        start_from = table['出入境日期'].iloc[-1]
    if end_at is None:
        end_at = table['出入境日期'].iloc[0]

    # 只要start_from和end_at（包括）之间的记录
    table = table[table['出入境日期'] >= pd.Timestamp(start_from)]
    table = table[table['出入境日期'] <= pd.Timestamp(end_at)]

    # 在头尾两端插入虚拟节点，其中在最晚之后的节点插入end_at，设置出入境与最晚一次相反；在最早之前的节点插入start_from，设置出入境与最早一次相反
    virtual_latest: list = [table['序号'].min() - 1, '入境' if table['出境/入境'].iloc[0] == '出境' else '出境', pd.Timestamp(end_at)]
    virtual_earliest: list = [table['序号'].max() + 1, '入境' if table['出境/入境'].iloc[-1] == '出境' else '出境', pd.Timestamp(start_from)]

    # 如果说最晚一次是入境，那么前面的虚拟节点其实是可以不用添加的；反之，如果是出境，那么一定要添加入境的虚拟节点
    if table['出境/入境'].iloc[0] == '出境':
        table = pd.concat([pd.DataFrame([virtual_latest], columns=table.columns), table], ignore_index=True)
    # 如果说最早一次是出境，那么后面的虚拟节点其实是可以不用添加的；反之，如果是入境，那么一定要添加出境的虚拟节点
    if table['出境/入境'].iloc[-1] == '入境':
        table = pd.concat([table, pd.DataFrame([virtual_earliest], columns=table.columns)], ignore_index=True)

    # 这样，我们就保证了最晚的一定是入境，最早的一定是出境，封闭了时间段
    return table

def convert_date_to_index(duration_begin: pd.Timestamp, duration_end: pd.Timestamp, start_from: pd.Timestamp) -> list[int]:
    '''
    将日期转换为从开始日期到结束日期的天数

    Parameters:
    duration_begin: pd.Timestamp, 持续时间的开始日期
    duration_end: pd.Timestamp, 持续时间的结束日期
    start_from: pd.Timestamp，整段时间的开始日期，计算bias的时候需要用到
    '''

    # 先计算bias，然后再计算天数
    bias = (duration_begin - start_from).days

    result: list[int] = []
    for date in pd.date_range(duration_begin, duration_end, inclusive='both'):
        index: int = (date - duration_begin).days + bias
        result.append(index)

    return result

def calculate_duration(table: pd.DataFrame, exclude_holidays: bool, course_only: bool) -> int:
    '''
    计算不在境内的天数，包括非工作日，我们保证最早的是出境，最晚的是入境

    Parameters:
    table: pd.DataFrame, 出入境记录的DataFrame
    exclude_holidays: bool, 是否排除非工作日
    course_only: bool, 是否只计算上课时间
    '''

    # 排序，按照序号的升序，保证最晚的日期在最前面
    table.sort_values(by='序号', ascending=True, inplace=True)

    start_from: pd.Timestamp = table['出入境日期'].iloc[-1]
    end_at: pd.Timestamp = table['出入境日期'].iloc[0]

    # 生成一个bool数组，长度是从最早到最晚的天数，包括最早和最晚，这个主要是为了避免一日内多次出入境的情况
    days: list = [False] * ((end_at - start_from).days + 1)

    last_entry: pd.Timestamp = None

    for index, row in table.iterrows():
        if row['出境/入境'] == '入境':
            last_entry = row['出入境日期']
        else:
            indexes: list = convert_date_to_index(row['出入境日期'], last_entry, start_from)
            for index in indexes:
                days[index] = True

    # 讲日期里面全部的非工作日都标记为False
    if exclude_holidays:
        for index, date in enumerate(pd.date_range(start_from, end_at, inclusive='both')):
            if not is_workday(date):
                days[index] = False

    # 讲日期里面全部的非上课时间都标记为False
    if course_only:
        for index, date in enumerate(pd.date_range(start_from, end_at, inclusive='both')):
            if not is_course(date):
                days[index] = False

    # 把全部的days中为True的天数加起来
    return sum(days)

def main():
    pdf_path = 'assets/dcc8c7e7e13e5aaba5adcf82e5124e37.pdf'
    table = extract_tables_from_pdf(pdf_path, start_from='2024-09-02', end_at='2024-12-31')
    table.to_csv('assets/extracted_table.csv', index=False)
    print('包括非工作日的停留天数:', calculate_duration(table, exclude_holidays=False, course_only=False))
    print('仅包括工作日的停留天数:', calculate_duration(table, exclude_holidays=True, course_only=False))
    print('仅包括上课时间的停留天数:', calculate_duration(table, exclude_holidays=False, course_only=True))
    print('仅包括工作日和上课时间的停留天数:', calculate_duration(table, exclude_holidays=True, course_only=True))

if __name__ == '__main__':
    main()
