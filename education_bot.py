import os
from dotenv import load_dotenv
import mysql.connector
import networkx as nx
import matplotlib.pyplot as plt
from io import BytesIO
import lancedb
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import LanceDB
from langchain_community.tools import TavilySearchResults
from langchain.agents import create_openai_tools_agent, AgentExecutor
from langchain_core.output_parsers import StrOutputParser
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from openai import OpenAI
import openai

# Загрузка переменных окружения
load_dotenv()

# Инициализация моделей и инструментов
llm = ChatOpenAI(model="gpt-4", temperature=0.7)
embedding = OpenAIEmbeddings()
search = TavilySearchResults()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Подключение к MySQL
def connect_to_db():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME")
    )

# Подключение к LanceDB
def connect_to_lancedb():
    db = lancedb.connect(os.getenv("LANCE_DB_PATH"))
    return db.open_table("pdf_docs")

# Функции для работы с курсами
def get_all_courses():
    conn = connect_to_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM course")
    courses = cursor.fetchall()
    conn.close()
    return courses

def get_course_topics(course_id):
    conn = connect_to_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM topic WHERE course_id = %s ORDER BY position", (course_id,))
    topics = cursor.fetchall()
    conn.close()
    return topics

def get_user_progress(user_id, course_id):
    conn = connect_to_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT t.topic_id, t.topic_name, uht.mark 
        FROM topic t
        LEFT JOIN user_has_topic uht ON t.topic_id = uht.topic_id AND uht.user_id = %s
        WHERE t.course_id = %s
        ORDER BY t.position
    """, (user_id, course_id))
    progress = cursor.fetchall()
    conn.close()
    return progress

def update_user_mark(user_id, topic_id, mark):
    conn = connect_to_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO user_has_topic (user_id, topic_id, mark) 
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE mark = VALUES(mark)
    """, (user_id, topic_id, mark))
    conn.commit()
    conn.close()

# Генерация конспекта
def generate_summary(text):
    prompt = ChatPromptTemplate.from_messages([
        ("system", "Вы - помощник для создания конспектов. Создайте краткое изложение текста, выделяя ключевые моменты."),
        ("human", "{text}")
    ])
    chain = prompt | llm | StrOutputParser()
    return chain.invoke({"text": text})

# Код-ревью
def code_review(task, code):
    prompt = ChatPromptTemplate.from_messages([
        ("system", "Вы - опытный программист. Проведите ревью кода, укажите ошибки и предложите оптимизации."),
        ("human", "Задание: {task}\n\nКод:\n{code}")
    ])
    chain = prompt | llm | StrOutputParser()
    return chain.invoke({"task": task, "code": code})

# Поиск видео
def find_videos(topic):
    try:
        # Создаем правильный промпт для агента с учетом всех обязательных переменных
        prompt = ChatPromptTemplate.from_messages([
            ("system", """Ты помощник, который ищет образовательные видео на YouTube. 
            Используй предоставленные инструменты для поиска актуальной информации.
            Отвечай кратко и предоставляй только ссылки на YouTube."""),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad")
        ])
        
        # Создаем инструменты
        tools = [TavilySearchResults(max_results=3)]
        
        # Создаем агента с правильным промптом
        agent = create_openai_tools_agent(
            llm=llm,
            tools=tools,
            prompt=prompt
        )
        
        # Создаем исполнителя агента
        agent_executor = AgentExecutor(
            agent=agent,
            tools=tools,
            verbose=True,
            handle_parsing_errors=True
        )
        
        # Выполняем поиск
        result = agent_executor.invoke({
            "input": f"Найди 3 лучших образовательных видео на YouTube по теме: {topic}",
            "agent_scratchpad": []
        })
        
        # Обрабатываем результат
        if "output" in result:
            # Извлекаем только ссылки на YouTube из результата
            import re
            youtube_links = re.findall(r'(https?://(?:www\.)?youtube\.com/watch\?v=[\w-]+)', result["output"])
            if youtube_links:
                return "\n".join(youtube_links[:3])  # Возвращаем не более 3 ссылок
            return result["output"]
        return "Не удалось найти видео по данной теме."
    except Exception as e:
        return "Произошла ошибка при поиске видео. Пожалуйста, попробуйте позже."

def create_embedding(text):
    response = client.embeddings.create(
        model="text-embedding-3-large",
        input=text,
        dimensions=1536
    )
    return response.data[0].embedding

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=60),
    retry=retry_if_exception_type((openai.RateLimitError, openai.APIError, openai.APIConnectionError))
)
def search_in_table(query_text, table, limit=3):
    try:
        query_embedding = create_embedding(query_text)
        results = table.search(query_embedding).limit(limit).to_pandas()
        return results
    except Exception as e:
        raise


# Поиск в векторной БД
def search_in_vector_db(query, db_path="lancedb", table_name="pdf_docs"):
    try:
        # Подключаемся к базе данных
        if not db_path:
            db_path = "lancedb"
            
        db = lancedb.connect(db_path)
        table = db.open_table(table_name)
        
        # Выполняем поиск
        results = search_in_table(query, table, limit=3)
        
        if results is None or len(results) == 0:
            return "Не удалось найти ответ в векторной базе данных"
        
        # Формируем контекст из найденных результатов
        context = "\n\n".join(results['text'].tolist())
        
        # Создаем промпт и цепочку для генерации ответа
        prompt = ChatPromptTemplate.from_messages([
            ("system", "Ответьте на вопрос пользователя на основе предоставленного контекста."),
            ("human", "Контекст:\n{context}\n\nВопрос: {query}")
        ])
        
        chain = prompt | llm | StrOutputParser()
        return chain.invoke({"context": context, "query": query})
        
    except Exception as e:
        return "Не удалось найти ответ в векторной базе данных"

# Генерация теста
def generate_test(topic_info):
    prompt = ChatPromptTemplate.from_messages([
        ("system", """
        Создайте тест из 6-10 вопросов по теме. Для каждого вопроса предоставьте 4 варианта ответа и укажите правильный.
        Формат: вопрос | вариант1 | вариант2 | вариант3 | вариант4 | правильный_ответ
        """),
        ("human", "Тема: {topic}")
    ])
    chain = prompt | llm | StrOutputParser()
    test = chain.invoke({"topic": topic_info})
    return [q.split("|") for q in test.split("\n") if q.strip()]

# Генерация графа курса
def generate_course_graph(course_id, user_id):
    topics = get_course_topics(course_id)
    progress = get_user_progress(user_id, course_id)
    
    G = nx.DiGraph()
    pos = {}
    
    for i, topic in enumerate(topics):
        topic_id = topic['topic_id']
        progress_data = next((p for p in progress if p['topic_id'] == topic_id), None)
        mark = progress_data['mark'] if progress_data else 0
        
        # Цвет в зависимости от прогресса
        if mark == None:
            color = '#ff0000'
        else:
            color = '#ff0000' if mark < 50 else '#ff9900' if mark < 80 else '#00aa00'
        
        G.add_node(topic['topic_name'], color=color, mark=mark)
        if i > 0:
            G.add_edge(topics[i-1]['topic_name'], topic['topic_name'])
        
        pos[topic['topic_name']] = (i, -i*0.5)
    
    plt.figure(figsize=(12, 8))
    colors = [G.nodes[n]['color'] for n in G.nodes()]
    labels = {n: f"{n}\n({G.nodes[n]['mark']}%)" for n in G.nodes()}
    
    nx.draw(G, pos, with_labels=True, labels=labels, node_size=3000, 
            node_color=colors, font_size=8, font_weight='bold', 
            arrowsize=20, edge_color='gray')
    
    img_bytes = BytesIO()
    plt.savefig(img_bytes, format='png')
    plt.close()
    img_bytes.seek(0)
    return img_bytes

# Объяснение темы
def explain_topic(topic_info):
    prompt = ChatPromptTemplate.from_messages([
        ("system", """
        Вы - преподаватель. Объясните тему студенту:
        1. Начните с краткого определения
        2. Приведите основные понятия
        3. Дайте примеры
        4. Будьте дружелюбны и используйте простой язык
        """),
        ("human", "Тема: {topic}")
    ])
    chain = prompt | llm | StrOutputParser()
    return chain.invoke({"topic": topic_info})
