import pandas as pd
from flask import Flask, render_template_string
import os
import plotly.graph_objs as go
import plotly.io as pio

app = Flask(__name__)

CSV_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'electricity_data.csv')

TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-cn">
<head>
    <meta charset="UTF-8">
    <title>南京大学电费监控面板</title>
    <style>
        body { font-family: 'Segoe UI', '微软雅黑', Arial, sans-serif; margin: 0; background: linear-gradient(120deg, #0f2027, #2c5364 80%); min-height: 100vh; }
        .container { max-width: 950px; margin: 48px auto; background: rgba(20, 30, 48, 0.95); border-radius: 18px; box-shadow: 0 6px 32px #0ff2ff33; padding: 38px 44px; }
        h1 { text-align: center; color: #00eaff; margin-bottom: 12px; letter-spacing: 2px; text-shadow: 0 2px 8px #0ff2ff44; }
        .desc { text-align: center; color: #b2e6ff; margin-bottom: 32px; font-size: 1.1em; }
        table { border-collapse: collapse; width: 100%; margin: 36px 0 0 0; background: rgba(10, 20, 40, 0.95); border-radius: 10px; overflow: hidden; }
        th, td { border: 1px solid #1de9b6; padding: 12px 18px; text-align: center; color: #fff; }
        th { background: linear-gradient(90deg, #00eaff 60%, #1de9b6 100%); color: #fff; font-weight: bold; letter-spacing: 1px; }
        td { color: #fff; }
        caption { font-size: 1.25em; margin-bottom: 12px; font-weight: bold; color: #00eaff; }
        tr:nth-child(even) { background: rgba(0, 234, 255, 0.07); }
        tr:nth-child(odd) { background: rgba(29, 233, 182, 0.07); }
        .chart-block { text-align: center; margin: 36px 0 10px 0; }
        .chart-block iframe, .chart-block div { border-radius: 12px; box-shadow: 0 2px 18px #00eaff33; background: #fff; }
        .reload-btn {
            display: inline-block;
            margin: 0 0 18px 0;
            padding: 8px 22px;
            font-size: 1em;
            color: #00eaff;
            background: linear-gradient(90deg, #232526 0%, #1de9b6 100%);
            border: none;
            border-radius: 8px;
            box-shadow: 0 2px 8px #00eaff33;
            cursor: pointer;
            transition: background 0.2s, color 0.2s;
        }
        .reload-btn:hover {
            background: linear-gradient(90deg, #1de9b6 0%, #00eaff 100%);
            color: #232526;
        }
        @media (max-width: 800px) {
            .container { padding: 10px; }
            table, th, td { font-size: 13px; }
        }
        .hidden-rows { display: none; }
        .show-more-btn { cursor: pointer; color: #00eaff; text-decoration: underline; }
        .highlight-row {
            font-weight: bold;
            background-color: rgba(255, 69, 0, 0.3); /* 更加突出的橙红色背景 */
            border: 2px solid #ff4500; /* 添加边框 */
        }
        .faint-text {
            color: rgba(255, 255, 255, 0.4); /* 浅色文本 */
            font-size: 0.9em; /* 较小字体 */
        }
    </style>
    <script>
        function toggleRows() {
            const hiddenRows = document.querySelectorAll('.hidden-rows');
            hiddenRows.forEach(row => {
                if (row.style.display === 'none' || row.style.display === '') {
                    row.style.display = 'table-row';
                } else {
                    row.style.display = 'none';
                }
            });
            const btn = document.getElementById('toggle-btn');
            btn.textContent = btn.textContent === '展开全部' ? '折叠' : '展开全部';
        }
    </script>
</head>
<body>
    <div class="container">
        <h1>南京大学电费监控面板</h1>
        <div class="desc">展示最近电费数据及变化趋势</div>
        <div class="chart-block">
            <button class="reload-btn" onclick="location.reload()">更新/重新加载</button>
            {{ plot_div|safe }}
        </div>
        <table>
            <caption>电费数据明细</caption>
            <tr>
                <th>日期</th>
                <th>时刻</th>
                <th>剩余电量</th>
                <th>电量使用</th>
                <th>单位</th>
            </tr>
            {% for row in visible_rows %}
            <tr class="{% if loop.index0 % 20 == 0 %}highlight-row{% endif %}">
                <td>{{ row['date'] }}</td>
                <td>{{ row['clock'] }}</td>
                <td>{{ '%.2f' % row['num'] }}</td>
                <td class="{% if row['difference'] is none %}faint-text{% endif %}">
                    {{ '%.2f' % row['difference'] if row['difference'] is not none else 'N/A' }}
                </td>
                <td>{{ row['unit'] }}</td>
            </tr>
            {% endfor %}
            {% for row in hidden_rows %}
            <tr class="hidden-rows {% if loop.index0 % 20 == 0 %}highlight-row{% endif %}">
                <td>{{ row['date'] }}</td>
                <td>{{ row['clock'] }}</td>
                <td>{{ '%.2f' % row['num'] }}</td>
                <td class="{% if row['difference'] is none %}faint-text{% endif %}">
                    {{ '%.2f' % row['difference'] if row['difference'] is not none else 'N/A' }}
                </td>
                <td>{{ row['unit'] }}</td>
            </tr>
            {% endfor %}
        </table>
        <div style="text-align: center; margin-top: 10px;">
            <span id="toggle-btn" class="show-more-btn" onclick="toggleRows()">展开全部</span>
        </div>
    </div>
</body>
</html>
"""

@app.route("/")
def index():
    df = pd.read_csv(CSV_PATH)
    df['time'] = pd.to_datetime(df['time'])  # 确保时间列为datetime类型
    df_sorted = df.sort_values('time')
    # Generate plot for 最近20次电量变化曲线
    recent_20 = df_sorted.tail(20).copy()
    recent_20_trace = go.Scatter(
        x=recent_20['time'],
        y=recent_20['num'],
        mode='lines+markers',
        marker=dict(color='#ff4500', size=9, line=dict(width=2, color='#ff6347')),
        line=dict(width=3, color='#ff6347'),
        hovertemplate='时间: %{x|%Y-%m-%d %H:%M:%S}<br>剩余电量: %{y} 度',
        name='最近20次电量变化',
        text=None,
        showlegend=False
    )
    recent_20_layout = go.Layout(
        title=dict(text='最近20次电量变化曲线', x=0.5, font=dict(family='Segoe UI,微软雅黑', size=20, color='#ff4500')),
        xaxis=dict(
            title='时间', 
            tickformat='%Y-%m-%d %H:%M', 
            tickangle=30, 
            showgrid=True, 
            gridcolor='rgba(255,69,0,0.15)', 
            gridwidth=1,
            griddash='dash',
            color='#ff6347', 
            tickfont=dict(color='#ff6347')
        ),
        yaxis=dict(
            title='剩余电量 (度)', 
            showgrid=True, 
            gridcolor='rgba(255,69,0,0.15)', 
            gridwidth=1,
            griddash='dash',
            color='#ff6347', 
            tickfont=dict(color='#ff6347')
        ),
        hovermode='x unified',
        plot_bgcolor='rgba(10,20,40,0.95)',
        paper_bgcolor='rgba(20,30,48,0.95)',
        margin=dict(l=60, r=30, t=60, b=60),
        font=dict(family='Segoe UI,微软雅黑', size=14, color='#ff6347')
    )
    recent_20_fig = go.Figure(data=[recent_20_trace], layout=recent_20_layout)
    recent_20_plot_div = pio.to_html(recent_20_fig, full_html=False, include_plotlyjs='cdn', config={
        'displayModeBar': True,
        'scrollZoom': True,
        'displaylogo': False,
        'modeBarButtonsToRemove': ['select2d', 'lasso2d', 'autoScale2d', 'resetScale2d', 'toggleSpikelines']
    })

    # 生成plotly曲线，科技感配色
    trace = go.Scatter(
        x=df_sorted['time'],
        y=df_sorted['num'],
        mode='lines+markers',
        marker=dict(color='#00eaff', size=9, line=dict(width=2, color='#1de9b6')),
        line=dict(width=3, color='#1de9b6'),
        hovertemplate='时间: %{x|%Y-%m-%d %H:%M:%S}<br>剩余电量: %{y} 度',
        name='剩余电量',
        text=None,
        showlegend=False
    )
    layout = go.Layout(
        title=dict(text='电量变化曲线', x=0.5, font=dict(family='Segoe UI,微软雅黑', size=20, color='#00eaff')),
        xaxis=dict(
            title='时间', 
            tickformat='%Y-%m-%d %H:%M', 
            tickangle=30, 
            showgrid=True, 
            gridcolor='rgba(29,233,182,0.15)', 
            gridwidth=1,
            griddash='dash',
            color='#b2e6ff', 
            tickfont=dict(color='#b2e6ff')
        ),
        yaxis=dict(
            title='剩余电量 (度)', 
            showgrid=True, 
            gridcolor='rgba(29,233,182,0.15)', 
            gridwidth=1,
            griddash='dash',
            color='#b2e6ff', 
            tickfont=dict(color='#b2e6ff')
        ),
        hovermode='x unified',
        plot_bgcolor='rgba(10,20,40,0.95)',
        paper_bgcolor='rgba(20,30,48,0.95)',
        margin=dict(l=60, r=30, t=60, b=60),
        font=dict(family='Segoe UI,微软雅黑', size=14, color='#b2e6ff')
    )
    fig = go.Figure(data=[trace], layout=layout)
    plot_div = pio.to_html(fig, full_html=False, include_plotlyjs='cdn', config={
        'displayModeBar': True,
        'scrollZoom': True,
        'displaylogo': False,
        'modeBarButtonsToRemove': ['select2d', 'lasso2d', 'autoScale2d', 'resetScale2d', 'toggleSpikelines']
    })
    # 格式化时间为字符串用于表格展示
    df_sorted['time'] = df_sorted['time'].dt.strftime('%Y-%m-%d %H:%M:%S')
    df_sorted = df.sort_values('time', ascending=False)
    df_sorted['date'] = df_sorted['time'].dt.date
    df_sorted['clock'] = df_sorted['time'].dt.strftime('%H:%M:%S')  # 确保时刻显示为整数秒
    df_sorted['num'] = df_sorted['num'].round(2)  # 保留剩余电量小数点后两位
    df_sorted['difference'] = df_sorted['num'].diff().round(2)  # 计算所有行的电量变化并保留两位小数
    # df_sorted['difference'] = df_sorted['difference'].where(df_sorted.index % 20 == 0, None)  # 非20倍数行显示为None
    rows = df_sorted.to_dict(orient="records")
    visible_rows = rows[:20]
    hidden_rows = rows[20:]
    return render_template_string(TEMPLATE, visible_rows=visible_rows, hidden_rows=hidden_rows, plot_div=recent_20_plot_div + plot_div)

if __name__ == "__main__":
    app.run(debug=True)
