import os
import telebot
from telebot import types
from education_bot import *

bot = telebot.TeleBot(os.getenv("TELEGRAM_BOT_TOKEN"))

# Состояния пользователей
user_states = {}
current_tests = {}

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username or str(user_id)
    
    conn = connect_to_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT IGNORE INTO user (user_id, username) VALUES (%s, %s)", (user_id, username))
        conn.commit()
    except:
        pass
    finally:
        conn.close()
    
    main_menu(message)

def main_menu(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('Решение задач', 'Подбор видео')
    markup.row('Конспект лекции', 'Код-ревью')
    markup.row('Ответы на вопросы', 'Прохождение курсов')
    bot.send_message(message.chat.id, "Выберите действие:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == 'Решение задач')
def problem_solving(message):
    # Устанавливаем состояние "в режиме решения задач"
    user_id = message.from_user.id
    user_states[user_id] = {'mode': 'problem_solving'}
    
    msg = bot.send_message(
        message.chat.id, 
        "Опишите задачу, которую нужно решить. Я буду задавать наводящие вопросы, чтобы помочь вам найти решение самостоятельно.\n\n"
        "Когда закончите, напишите 'Завершить' или нажмите соответствующую кнопку.",
        reply_markup=create_problem_solving_keyboard()
    )
    bot.register_next_step_handler(msg, handle_problem_solving)

def create_problem_solving_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('Завершить')
    return markup

def handle_problem_solving(message):
    user_id = message.from_user.id
    
    # Проверяем, хочет ли пользователь завершить
    if message.text.lower() in ['завершить', 'закончить', 'выход']:
        bot.send_message(
            message.chat.id, 
            "Завершаю режим решения задач. Если нужно будет снова помочь - просто выберите 'Решение задач' в меню.",
            reply_markup=types.ReplyKeyboardRemove()
        )
        # Очищаем состояние
        if user_id in user_states:
            del user_states[user_id]
        main_menu(message)
        return
    
    # Если пользователь не завершает, продолжаем помогать
    prompt = ChatPromptTemplate.from_messages([
        ("system", """
        Вы - преподаватель. Помогите студенту решить задачу, задавая наводящие вопросы.
        Не давайте готового решения, только направляйте.
        Если студент прислал ответ на ваш предыдущий вопрос, проанализируйте его и задайте следующий уточняющий вопрос.
        Будьте дружелюбны и терпеливы.
        """),
        ("human", "{problem}")
    ])
    chain = prompt | llm | StrOutputParser()
    
    # Получаем историю предыдущих сообщений в этом режиме
    history = user_states.get(user_id, {}).get('problem_history', [])
    history.append(f"Пользователь: {message.text}")
    full_context = "\n".join(history[-5:])  # Берем последние 5 сообщений для контекста
    
    response = chain.invoke({"problem": full_context})
    
    history.append(f"Ассистент: {response}")
    user_states[user_id]['problem_history'] = history
    
    msg = bot.send_message(
        message.chat.id, 
        response + "\n\nПродолжайте отвечать на вопросы или напишите 'Завершить', чтобы закончить.",
        reply_markup=create_problem_solving_keyboard()
    )
    bot.register_next_step_handler(msg, handle_problem_solving)

@bot.message_handler(func=lambda m: m.text == 'Конспект лекции')
def lecture_summary(message):
    msg = bot.send_message(message.chat.id, "Отправьте текст лекции для создания конспекта:")
    bot.register_next_step_handler(msg, process_lecture)

def process_lecture(message):
    summary = generate_summary(message.text)
    bot.send_message(message.chat.id, f"Краткий конспект:\n\n{summary}")
    main_menu(message)

@bot.message_handler(func=lambda m: m.text == 'Код-ревью')
def code_review_menu(message):
    msg = bot.send_message(message.chat.id, "Отправьте описание задания:")
    bot.register_next_step_handler(msg, get_code_for_review)

def get_code_for_review(message):
    user_states[message.from_user.id] = {'task': message.text}
    msg = bot.send_message(message.chat.id, "Теперь отправьте ваш код:")
    bot.register_next_step_handler(msg, process_code_review)

def process_code_review(message):
    user_id = message.from_user.id
    task = user_states.get(user_id, {}).get('task', '')
    review = code_review(task, message.text)
    bot.send_message(message.chat.id, f"Результат ревью:\n\n{review}")
    main_menu(message)

@bot.message_handler(func=lambda m: m.text == 'Подбор видео')
def video_search(message):
    msg = bot.send_message(
        message.chat.id, 
        "Введите тему для поиска видео (например: 'машинное обучение для начинающих'):",
        reply_markup=types.ReplyKeyboardRemove()
    )
    bot.register_next_step_handler(msg, process_video_search)

def process_video_search(message):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        
        videos = find_videos(message.text)
        
        if videos.startswith("http"):
            response = f"Найденные видео по теме '{message.text}':\n\n{videos}"
        else:
            response = videos
            
        bot.send_message(
            message.chat.id, 
            response,
            disable_web_page_preview=False  # Позволяем показывать превью ссылок
        )
    except Exception as e:
        bot.send_message(
            message.chat.id, 
            "Произошла ошибка при поиске видео. Пожалуйста, попробуйте позже."
        )
    main_menu(message)

@bot.message_handler(func=lambda m: m.text == 'Ответы на вопросы')
def question_answering(message):
    msg = bot.send_message(message.chat.id, "Задайте ваш вопрос:")
    bot.register_next_step_handler(msg, process_question)

def process_question(message):
    answer = search_in_vector_db(message.text)
    bot.send_message(message.chat.id, answer)
    main_menu(message)

@bot.message_handler(func=lambda m: m.text == 'Прохождение курсов')
def courses_menu(message):
    courses = get_all_courses()
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for course in courses:
        markup.row(f"Курс: {course['course_name']} (ID: {course['course_id']})")
    markup.row('Назад в главное меню')
    bot.send_message(message.chat.id, "Выберите курс:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text.startswith('Курс:'))
def course_selected(message):
    course_id = int(message.text.split("ID: ")[1][:-1])
    user_id = message.from_user.id
    user_states[user_id] = {
        'course_id': course_id,
        'action': None
    }
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('Граф курса')
    markup.row('Объяснить тему')
    markup.row('Пройти тест')
    markup.row('Назад к курсам')
    bot.send_message(message.chat.id, "Выберите действие с курсом:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == 'Граф курса')
def show_course_graph(message):
    user_id = message.from_user.id
    course_id = user_states.get(user_id, {}).get('course_id')
    
    if course_id:
        img_bytes = generate_course_graph(course_id, user_id)
        bot.send_photo(message.chat.id, img_bytes)
    else:
        bot.send_message(message.chat.id, "Ошибка: курс не выбран.")
    courses_menu(message)

@bot.message_handler(func=lambda m: m.text == 'Объяснить тему')
def explain_topic_menu(message):
    user_id = message.from_user.id
    course_id = user_states.get(user_id, {}).get('course_id')
    
    if not course_id:
        bot.send_message(message.chat.id, "Ошибка: курс не выбран.")
        courses_menu(message)
        return
    
    topics = get_course_topics(course_id)
    if not topics:
        bot.send_message(message.chat.id, "В этом курсе нет тем для объяснения.")
        course_selected(message)
        return
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for topic in topics:
        markup.row(topic['topic_name'])
    markup.row('Назад к курсу')
    bot.send_message(message.chat.id, "Выберите тему для объяснения:", reply_markup=markup)

@bot.message_handler(func=lambda m: any(t['topic_name'] == m.text for t in get_all_topics()) and user_states.get(m.from_user.id, {}).get('mode') != 'topic_qa')
def start_test(message):
    user_id = message.from_user.id
    topic_name = message.text

    if user_states.get(user_id, {}).get('action') != 'take_test':
        process_topic_explanation(message)
        return
    
    # Получаем информацию о теме
    conn = connect_to_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM topic WHERE topic_name = %s", (topic_name,))
    topic = cursor.fetchone()
    conn.close()
    
    if topic:
        test = generate_test(topic['text'])
        current_tests[user_id] = {
            'topic_id': topic['topic_id'],
            'test': test,
            'answers': [],
            'current_question': 0,
            'score': 0,
            'course_id': user_states.get(user_id, {}).get('course_id')
        }
        ask_question(message, user_id)
    else:
        bot.send_message(message.chat.id, "Ошибка: тема не найдена.")

@bot.message_handler(func=lambda m: any(t['topic_name'] == m.text for t in get_all_topics()))
def process_topic_explanation(message):
    topic_name = message.text
    explanation = explain_topic(topic_name)
    bot.send_message(message.chat.id, explanation)
    
    user_id = message.from_user.id
    user_states[user_id] = {
        'topic': topic_name,
        'mode': 'topic_qa',
        'course_id': user_states.get(user_id, {}).get('course_id')
    }
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('Выход')
    
    msg = bot.send_message(
        message.chat.id, 
        "Вы можете задавать вопросы по этой теме. Нажмите 'Выход' для завершения.",
        reply_markup=markup
    )
    bot.register_next_step_handler(msg, handle_topic_questions)

def handle_topic_questions(message):
    user_id = message.from_user.id
    user_state = user_states.get(user_id, {})
    
    if message.text.lower() in ['выход', '/exit']:
        if 'course_id' in user_state:
            course_id = user_state['course_id']
            user_states[user_id] = {
                'course_id': course_id,
                'action': None 
            }
            
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
            markup.row('Граф курса')
            markup.row('Объяснить тему')
            markup.row('Пройти тест')
            markup.row('Назад к курсам')
            
            conn = connect_to_db()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT course_name FROM course WHERE course_id = %s", (course_id,))
            course = cursor.fetchone()
            conn.close()
            
            if course:
                bot.send_message(
                    message.chat.id, 
                    f"Вы вернулись в меню курса: {course['course_name']}",
                    reply_markup=markup
                )
            else:
                bot.send_message(message.chat.id, "Вы вернулись в меню курса", reply_markup=markup)
        else:
            main_menu(message)
        return
    
    topic = user_state.get('topic', '')
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", f"Вы - преподаватель. Отвечайте на вопросы по теме '{topic}'."),
        ("human", "{question}")
    ])
    chain = prompt | llm | StrOutputParser()
    response = chain.invoke({"question": message.text})
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('Выход')
    
    msg = bot.send_message(
        message.chat.id, 
        response + "\n\nМожете задать еще вопрос или нажмите 'Выход'",
        reply_markup=markup
    )
    bot.register_next_step_handler(msg, handle_topic_questions)

@bot.message_handler(func=lambda m: m.text == 'Пройти тест')
def take_test_menu(message):
    user_id = message.from_user.id
    course_id = user_states.get(user_id, {}).get('course_id')
    
    if course_id:
        # Устанавливаем режим тестирования
        user_states[user_id] = {
            'course_id': course_id,
            'action': 'take_test'
        }
        
        topics = get_course_topics(course_id)
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for topic in topics:
            markup.row(topic['topic_name'])
        markup.row('Назад к курсу')
        bot.send_message(message.chat.id, "Выберите тему для тестирования:", reply_markup=markup)
    else:
        bot.send_message(message.chat.id, "Ошибка: курс не выбран.")
        courses_menu(message)

def ask_question(message, user_id):
    test_data = current_tests[user_id]
    question_data = test_data['test'][test_data['current_question']]
    question = question_data[0].strip()
    options = [opt.strip() for opt in question_data[1:5]]
    correct = question_data[5].strip()
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for i, opt in enumerate(options):
        markup.row(f"{i+1}. {opt}")
    
    bot.send_message(message.chat.id, f"Вопрос {test_data['current_question']+1}/{len(test_data['test'])}:\n{question}", reply_markup=markup)
    bot.register_next_step_handler(message, process_test_answer)

def process_test_answer(message):
    user_id = message.from_user.id
    test_data = current_tests.get(user_id)
    
    if not test_data:
        bot.send_message(message.chat.id, "Тест прерван.")
        return
    
    question_data = test_data['test'][test_data['current_question']]
    correct_answer = question_data[5].strip()
    user_answer = message.text.split('.')[1].strip()
    
    # Проверяем ответ
    if user_answer == correct_answer:
        test_data['score'] += 1
    
    test_data['current_question'] += 1
    test_data['answers'].append((question_data[0], user_answer, correct_answer))
    
    if test_data['current_question'] < len(test_data['test']):
        ask_question(message, user_id)
    else:
        finish_test(message, user_id)

def finish_test(message, user_id):
    test_data = current_tests[user_id]
    score = test_data['score']
    total = len(test_data['test'])
    percentage = int((score / total) * 100)
    
    update_user_mark(user_id, test_data['topic_id'], percentage)
    
    report = f"Тест завершен!\nРезультат: {score}/{total} ({percentage}%)\n\n"
    report += "Подробные ответы:\n"
    
    for i, (question, user_ans, correct_ans) in enumerate(test_data['answers']):
        report += f"\n{i+1}. {question}\nВаш ответ: {user_ans}\nПравильный ответ: {correct_ans}\n"
    
    bot.send_message(message.chat.id, report)
    del current_tests[user_id]
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('Выход')
    msg = bot.send_message(
        message.chat.id, 
        "Нажмите 'Выход' для завершения тестирования.",
        reply_markup=markup
    )
    bot.register_next_step_handler(msg, handle_topic_questions)

@bot.message_handler(func=lambda m: m.text == 'Назад к курсу')
def back_to_course(message):
    user_id = message.from_user.id
    course_id = user_states.get(user_id, {}).get('course_id')
    
    if course_id:
        user_states[user_id] = {
            'course_id': course_id,
            'action': None
        }
        
        conn = connect_to_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT course_name FROM course WHERE course_id = %s", (course_id,))
        course = cursor.fetchone()
        conn.close()
        
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.row('Граф курса')
        markup.row('Объяснить тему')
        markup.row('Пройти тест')
        markup.row('Назад к курсам')
        
        if course:
            bot.send_message(
                message.chat.id, 
                f"Вы вернулись в меню курса: {course['course_name']}",
                reply_markup=markup
            )
        else:
            bot.send_message(message.chat.id, "Вы вернулись в меню курса", reply_markup=markup)
    else:
        bot.send_message(message.chat.id, "Ошибка: курс не найден.")
        courses_menu(message)

@bot.message_handler(func=lambda m: m.text == 'Назад к курсам')
def back_to_courses(message):
    courses_menu(message)

@bot.message_handler(func=lambda m: m.text == 'Назад в главное меню')
def back_to_main(message):
    main_menu(message)

def get_all_topics():
    conn = connect_to_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM topic")
    topics = cursor.fetchall()
    conn.close()
    return topics

if __name__ == '__main__':
    print("Бот запущен...")
    bot.infinity_polling()