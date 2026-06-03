/**
 * ============================================================================
 * ECharts 图表渲染器 — 支持 13 种图表类型
 * ============================================================================
 * 统一接口：renderChart(container, type, data, opts)
 * 返回 ECharts 实例以便后续更新/销毁
 * ============================================================================
 */

const CHART_THEME = {
    backgroundColor: 'transparent',
    textStyle: { color: '#2a3a4a' },
};

/* --- 多风格主题配色：三大色系 × 七种颜色 --- */
const CHART_STYLES = {
    blue:    { colors:['#3498db','#2ecc71','#e74c3c','#f39c12','#9b59b6','#1abc9c','#e67e22','#34495e'], group:'默认' },
    green:   { colors:['#27ae60','#52be80','#76d7c4','#1e8449','#229954','#148f77','#186a3b','#0e6251'], group:'默认' },
    red:     { colors:['#e74c3c','#f39c12','#f1c40f','#d35400','#e67e22','#c0392b','#a93226','#7b241c'], group:'默认' },
    orange:  { colors:['#e67e22','#f39c12','#f1c40f','#d35400','#e74c3c','#c0392b','#a93226','#922b21'], group:'默认' },
    purple:  { colors:['#8e44ad','#af7ac5','#d2b4de','#7d3c98','#6c3483','#bb8fce','#a569bd','#5b2c6f'], group:'默认' },
    cyan:    { colors:['#3498db','#2ecc71','#1abc9c','#2980b9','#16a085','#27ae60','#2471a3','#1a5276'], group:'默认' },
    gray:    { colors:['#5d6d7e','#85929e','#aeb6bf','#34495e','#2c3e50','#7f8c8d','#95a5a6','#bdc3c7'], group:'默认' },
    lightBlue:   { colors:['#aed6f1','#d4e6f1','#85c1e9','#a9cce3','#b4d7f0','#c5e0f7','#e0f0ff','#ebf5fb'], group:'浅色' },
    lightGreen:  { colors:['#a9dfbf','#d5f5e3','#82e0aa','#abebc6','#b8e6cc','#c8f0db','#e0f8f0','#e8f8f5'], group:'浅色' },
    lightPink:   { colors:['#f5b7b1','#fadbd8','#f1948a','#f5b7b1','#f8ccc4','#fadbd8','#fce8e8','#fdedec'], group:'浅色' },
    lightOrange: { colors:['#f9e79f','#fef9e7','#f7dc6f','#fad7a0','#fce4b3','#fef0c8','#fff8e0','#fef9e7'], group:'浅色' },
    lightPurple: { colors:['#d7bde2','#e8daef','#bb8fce','#d2b4de','#d8c0e0','#e8d4f0','#f0e8f8','#f4ecf7'], group:'浅色' },
    lightCyan:   { colors:['#a3e4d7','#d1f2eb','#76d7c4','#aed6f1','#b8e0d4','#c8f0e8','#e0f8f4','#e8f8f5'], group:'浅色' },
    lightGray:   { colors:['#d5dbdb','#ebedef','#b2babb','#d5f5e3','#c8d0d4','#dce0e4','#eef0f2','#f0f3f4'], group:'浅色' },
    darkBlue:    { colors:['#1a5276','#154360','#1b4f72','#2471a3','#0c2461','#1a3a6a','#2c3e50','#17202a'], group:'深色' },
    darkGreen:   { colors:['#1e8449','#145a32','#186a3b','#239b56','#0a3d20','#1a5a3a','#1e8449','#0b5345'], group:'深色' },
    darkRed:     { colors:['#922b21','#641e16','#b03a2e','#c0392b','#7b241c','#8a1a1a','#78281f','#17202a'], group:'深色' },
    darkOrange:  { colors:['#935116','#6b3a00','#a04000','#ca6f1e','#4a2500','#7a3a00','#7b4a00','#17202a'], group:'深色' },
    darkPurple:  { colors:['#6c3483','#4a235a','#7d3c98','#8e44ad','#3b1f47','#5a2a7a','#5b2c6f','#17202a'], group:'深色' },
    darkCyan:    { colors:['#0e6655','#0a4d3e','#117864','#148f77','#08362a','#0a4a3a','#0b5345','#17202a'], group:'深色' },
    darkGray:    { colors:['#2c3e50','#1b2631','#212f3d','#273746','#0e1621','#1a2a3a','#17202a','#0d1117'], group:'深色' },
};
let _activeStyle = 'blue';
function setChartStyle(name) { if (CHART_STYLES[name]) { _activeStyle = name; return true; } return false; }
function getActiveStyle() { return _activeStyle; }
function getColors() { return CHART_STYLES[_activeStyle]?.colors || CHART_STYLES.blue.colors; }
function getStyleGroup(name) { return CHART_STYLES[name]?.group || '默认'; }

/**
 * 渲染图表
 * @param {string|HTMLElement} container - DOM ID 或元素
 * @param {string} type - 图表类型
 * @param {object} data - 数据
 * @param {object} opts - 选项覆盖
 * @returns {object} ECharts 实例
 */
function renderChart(container, type, data, opts = {}) {
    const dom = typeof container === 'string' ? document.getElementById(container) : container;
    if (!dom) return null;

    // 销毁已有实例
    const existing = echarts.getInstanceByDom(dom);
    if (existing) existing.dispose();

    const chart = echarts.init(dom, null, { renderer: 'canvas' });
    const option = buildOption(type, data, opts);
    if (option) {
        chart.setOption(option);
    }
    window.addEventListener('resize', () => chart.resize());
    return chart;
}

/**
 * 根据类型构建 ECharts option
 */
function buildOption(type, data, opts = {}) {
    const builder = {
        line: buildLineOption,
        bar: buildBarOption,
        pie: buildPieOption,
        scatter: buildScatterOption,
        funnel: buildFunnelOption,
        gauge: buildGaugeOption,
        heatmap: buildHeatmapOption,
        treemap: buildTreemapOption,
        radar: buildRadarOption,
        boxplot: buildBoxplotOption,
        sankey: buildSankeyOption,
        parallel: buildParallelOption,
        sunburst: buildSunburstOption,
        effectScatter: buildEffectScatterOption,
        candlestick: buildCandlestickOption,
        pictorialBar: buildPictorialBarOption,
        graph: buildGraphOption,
        themeRiver: buildThemeRiverOption,
    };
    const fn = builder[type];
    if (!fn) return buildBarOption(data, opts);
    return fn(data, opts);
}

/* --- 基础配色 --- */
const COLORS = ['#3498db', '#2ecc71', '#e74c3c', '#f39c12', '#9b59b6', '#1abc9c', '#e67e22', '#34495e'];

// Generate monochromatic color shades from a base hex color (for pie/funnel charts)
function generateShades(baseColor, count = 7) {
    const r = parseInt(baseColor.slice(1,3), 16), g = parseInt(baseColor.slice(3,5), 16), b = parseInt(baseColor.slice(5,7), 16);
    const shades = [];
    for (let i = 0; i < count; i++) {
        const t = i / (count - 1);
        shades.push(`rgb(${Math.min(255,Math.round(r+(255-r)*t*0.5))},${Math.min(255,Math.round(g+(255-g)*t*0.5))},${Math.min(255,Math.round(b+(255-b)*t*0.5))})`);
    }
    for (let i = 1; i <= 3; i++) {
        shades.push(`rgb(${Math.max(0,Math.round(r*(1-i*0.15)))},${Math.max(0,Math.round(g*(1-i*0.15)))},${Math.max(0,Math.round(b*(1-i*0.15)))})`);
    }
    return shades;
}

/* ========================================================================
   1. 折线图
   ======================================================================== */
function buildLineOption(data, opts) {
    return {
        ...CHART_THEME,
        tooltip: { trigger: 'axis' },
        legend: { data: data.seriesNames || ['数值'], textStyle: { color: '#4a5a6a' } },
        grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
        xAxis: { type: 'category', data: data.labels || [], axisLabel: { color: '#8892a8' } },
        yAxis: { type: 'value', splitLine: { lineStyle: { color: '#d0dce8' } }, axisLabel: { color: '#8892a8' } },
        series: (data.series || [{ data: data.values || [] }]).map((s, i) => ({
            type: 'line',
            smooth: true,
            symbol: 'circle',
            symbolSize: 6,
            data: s.data,
            name: s.name || `系列${i + 1}`,
            lineStyle: { width: 2, color: getColors()[i % getColors().length] },
            itemStyle: { color: getColors()[i % getColors().length] },
            areaStyle: { opacity: 0.08, color: getColors()[i % getColors().length] },
        })),
        ...opts.extraOptions,
    };
}

/* ========================================================================
   2. 柱状图
   ======================================================================== */
function buildBarOption(data, opts) {
    return {
        ...CHART_THEME,
        tooltip: { trigger: 'axis' },
        legend: { data: data.seriesNames || ['数值'], textStyle: { color: '#4a5a6a' } },
        grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
        xAxis: { type: 'category', data: data.labels || [], axisLabel: { color: '#8892a8', rotate: data.labels && data.labels.length > 8 ? 35 : 0 } },
        yAxis: { type: 'value', splitLine: { lineStyle: { color: '#d0dce8' } }, axisLabel: { color: '#8892a8' } },
        series: (data.series || [{ data: data.values || [] }]).map((s, i) => ({
            type: 'bar',
            barWidth: '50%',
            data: s.data,
            name: s.name || `系列${i + 1}`,
            itemStyle: {
                color: getColors()[i % getColors().length],
                borderRadius: [4, 4, 0, 0],
            },
        })),
        ...opts.extraOptions,
    };
}

/* ========================================================================
   3. 饼图
   ======================================================================== */
function buildPieOption(data, opts) {
    const items = data.labels && data.values
        ? data.labels.map((name, i) => ({ name, value: data.values[i] }))
        : data.items || [];
    // Monochromatic shades from first palette color (same色系)
    const shades = generateShades(getColors()[0], items.length || 7);
    items.forEach((item, i) => { item.itemStyle = { color: shades[i % shades.length] }; });
    return {
        ...CHART_THEME,
        tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
        legend: {
            data: items.map(i => i.name),
            textStyle: { color: '#4a5a6a' },
            bottom: 0,
        },
        series: [{
            type: 'pie',
            radius: ['35%', '60%'],
            center: ['50%', '48%'],
            roseType: opts.roseType || false,
            data: items,
            label: {
                color: '#2a3a4a',
                formatter: '{b}\n{d}%',
            },
            labelLine: { lineStyle: { color: '#b0c0d0' } },
            itemStyle: {
                borderRadius: 4,
                borderColor: '#d0dce8',
                borderWidth: 2,
            },
            emphasis: {
                itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.5)' },
            },
        }],
        ...opts.extraOptions,
    };
}

/* ========================================================================
   4. 散点图
   ======================================================================== */
function buildScatterOption(data, opts) {
    const points = data.points || (data.labels && data.values
        ? data.labels.map((x, i) => [x, data.values[i]]) : []);
    return {
        ...CHART_THEME,
        tooltip: { trigger: 'item', formatter: (p) => `X: ${p.value[0]}<br/>Y: ${p.value[1]}` },
        grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
        xAxis: { splitLine: { lineStyle: { color: '#d0dce8' } }, axisLabel: { color: '#8892a8' } },
        yAxis: { splitLine: { lineStyle: { color: '#d0dce8' } }, axisLabel: { color: '#8892a8' } },
        series: [{
            type: 'scatter',
            symbolSize: data.symbolSize || 10,
            data: points,
            itemStyle: { color: getColors()[0], opacity: 0.7 },
        }],
        ...opts.extraOptions,
    };
}

/* ========================================================================
   5. 漏斗图
   ======================================================================== */
function buildFunnelOption(data, opts) {
    const items = data.labels && data.values
        ? data.labels.map((name, i) => ({ name, value: data.values[i] }))
        : data.items || [];
    const shades = generateShades(getColors()[0], items.length || 5);
    items.forEach((item, i) => { item.itemStyle = { color: shades[i % shades.length] }; });
    return {
        ...CHART_THEME,
        tooltip: { trigger: 'item', formatter: '{b}: {c}' },
        legend: { data: items.map(i => i.name), textStyle: { color: '#4a5a6a' } },
        series: [{
            type: 'funnel',
            left: '10%',
            right: '10%',
            top: 30,
            bottom: 30,
            data: items,
            label: { color: '#fff', formatter: '{b}\n{c}' },
            labelLine: { length: 10 },
            itemStyle: { borderColor: '#d0dce8', borderWidth: 2 },
            emphasis: { label: { fontSize: 16 } },
        }],
        ...opts.extraOptions,
    };
}

/* ========================================================================
   6. 仪表盘
   ======================================================================== */
function buildGaugeOption(data, opts) {
    const value = typeof data === 'number' ? data : (data.value || 0);
    const max = data.max || 100;
    const name = data.name || '';
    return {
        ...CHART_THEME,
        series: [{
            type: 'gauge',
            startAngle: 220,
            endAngle: -40,
            min: 0,
            max: max,
            pointer: { show: true, length: '60%', width: 4 },
            progress: {
                show: true,
                width: 12,
                itemStyle: { color: value > max * 0.7 ? getColors()[2] : value > max * 0.4 ? getColors()[3] : getColors()[1] },
            },
            axisLine: {
                lineStyle: { width: 12, color: [
                    [value / max, value > max * 0.7 ? getColors()[2] : value > max * 0.4 ? getColors()[3] : getColors()[1]],
                    [1, '#2d3650'],
                ]},
            },
            axisTick: { show: false },
            splitLine: { length: 8, lineStyle: { color: '#9aabb8' } },
            axisLabel: { color: '#8892a8', distance: 20 },
            detail: {
                valueAnimation: true,
                formatter: `{value}${data.unit || ''}`,
                color: '#2a3a4a',
                fontSize: 20,
                offsetCenter: [0, '40%'],
            },
            title: { offsetCenter: [0, '65%'], color: '#8892a8', fontSize: 13 },
            data: [{ value, name }],
        }],
        ...opts.extraOptions,
    };
}

/* ========================================================================
   7. 热力图
   ======================================================================== */
function buildHeatmapOption(data, opts) {
    const hours = data.hours || data.xLabels || (data.labels || []);
    const days = data.days || data.yLabels || [];
    const values = data.data || data.values || [];
    return {
        ...CHART_THEME,
        tooltip: { position: 'top', formatter: (p) => `${p.data[1]} ~ ${p.data[0]}: ${p.data[2]}` },
        grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
        xAxis: { type: 'category', data: hours, axisLabel: { color: '#8892a8' }, splitArea: { show: true, areaStyle: { color: ['rgba(0,0,0,0)'] } } },
        yAxis: { type: 'category', data: days, axisLabel: { color: '#8892a8' }, splitArea: { show: true, areaStyle: { color: ['rgba(0,0,0,0)'] } } },
        visualMap: {
            min: 0,
            max: Math.max(...values.map(v => Array.isArray(v) ? v[2] : v), 1),
            calculable: true,
            orient: 'horizontal',
            left: 'center',
            bottom: 0,
            inRange: { color: ['#2d3650', '#3498db', '#2ecc71', '#f39c12', '#e74c3c'] },
            textStyle: { color: '#4a5a6a' },
        },
        series: [{
            type: 'heatmap',
            data: values,
            label: { show: values.length < 50, color: '#2a3a4a' },
            emphasis: { itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.5)' } },
        }],
        ...opts.extraOptions,
    };
}

/* ========================================================================
   8. 树图
   ======================================================================== */
function buildTreemapOption(data, opts) {
    return {
        ...CHART_THEME,
        tooltip: { formatter: (p) => `${p.name}: ${p.value}` },
        series: [{
            type: 'treemap',
            roam: true,
            data: data.children || data.items || [],
            label: { color: '#fff', fontWeight: 'bold' },
            upperLabel: { color: '#8892a8', fontSize: 12 },
            itemStyle: { borderColor: '#d0dce8', borderWidth: 2 },
            levels: [
                { colorSaturation: [0.3, 0.7], itemStyle: { borderWidth: 2, borderColor: '#d0dce8', gapWidth: 2 } },
            ],
        }],
        ...opts.extraOptions,
    };
}

/* ========================================================================
   9. 雷达图
   ======================================================================== */
function buildRadarOption(data, opts) {
    const indicators = data.indicators || (data.labels || []).map(n => ({ name: n }));
    const seriesData = data.series || [{ data: data.values || [], name: '' }];
    return {
        ...CHART_THEME,
        tooltip: {},
        legend: { data: seriesData.map(s => s.name || '').filter(Boolean), textStyle: { color: '#4a5a6a' } },
        radar: {
            indicator: indicators.map(ind => ({
                name: ind.name,
                max: ind.max || (seriesData.length ? Math.max(...seriesData.map(s => s.data[ind.name] !== undefined ? s.data[ind.name] : 0)) * 1.2 : 100),
            })),
            shape: 'polygon',
            splitNumber: 4,
            axisName: { color: '#2a3a4a' },
            splitLine: { lineStyle: { color: '#d0dce8' } },
            splitArea: { areaStyle: { color: ['rgba(52,152,219,0.02)', 'rgba(52,152,219,0.05)'] } },
            axisLine: { lineStyle: { color: '#2d3650' } },
        },
        series: [{
            type: 'radar',
            data: seriesData.map((s, i) => ({
                value: indicators.map(ind => s.data[ind.name] !== undefined ? s.data[ind.name] : (s.data[s.data.length] !== undefined ? s.data[i] : 0)),
                name: s.name || '',
                areaStyle: { opacity: 0.1, color: getColors()[i % getColors().length] },
                lineStyle: { color: getColors()[i % getColors().length] },
                itemStyle: { color: getColors()[i % getColors().length] },
            })),
        }],
        ...opts.extraOptions,
    };
}

/* ========================================================================
   10. 箱线图
   ======================================================================== */
function buildBoxplotOption(data, opts) {
    return {
        ...CHART_THEME,
        tooltip: { trigger: 'item' },
        grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
        xAxis: { type: 'category', data: data.labels || [], axisLabel: { color: '#8892a8' } },
        yAxis: { type: 'value', splitLine: { lineStyle: { color: '#d0dce8' } }, axisLabel: { color: '#8892a8' } },
        series: [{
            type: 'boxplot',
            data: data.values || [],
            itemStyle: { color: getColors()[0], borderColor: getColors()[1] },
            outlierItemStyle: { color: getColors()[2] },
        }],
        ...opts.extraOptions,
    };
}

/* ========================================================================
   11. 桑基图
   ======================================================================== */
function buildSankeyOption(data, opts) {
    return {
        ...CHART_THEME,
        tooltip: { trigger: 'item', formatter: (p) => `${p.data.source || p.name} → ${p.data.target || ''}: ${p.data.value || ''}` },
        series: [{
            type: 'sankey',
            layout: 'none',
            emphasis: { focus: 'adjacency' },
            nodeAlign: 'left',
            data: data.nodes || [],
            links: data.links || [],
            lineStyle: { color: 'gradient', curveness: 0.5 },
            label: { color: '#2a3a4a', fontSize: 12 },
            itemStyle: { borderWidth: 1, borderColor: '#d0dce8' },
        }],
        ...opts.extraOptions,
    };
}

/* ========================================================================
   12. 平行坐标图
   ======================================================================== */
function buildParallelOption(data, opts) {
    const dims = data.dimensions || [];
    return {
        ...CHART_THEME,
        tooltip: { formatter: (p) => dims.map((d, i) => `${d}: ${p.data[i]}`).join('<br/>') },
        parallel: {
            axis: dims.map((name, i) => ({
                dim: i,
                name,
                nameTextStyle: { color: '#8892a8' },
                axisLabel: { color: '#8892a8' },
                splitLine: { lineStyle: { color: '#d0dce8' } },
            })),
            parallelAxisDefault: { axisLabel: { color: '#8892a8' } },
        },
        series: [{
            type: 'parallel',
            lineStyle: { width: 1, opacity: 0.4 },
            data: data.values || [],
            emphasis: { lineStyle: { width: 3, opacity: 0.8 } },
        }],
        ...opts.extraOptions,
    };
}

/* ========================================================================
   13. 旭日图
   ======================================================================== */
function buildSunburstOption(data, opts) {
    return {
        ...CHART_THEME,
        tooltip: { formatter: (p) => `${p.name}: ${p.value}` },
        series: [{
            type: 'sunburst',
            data: data.children || data.items || [{ name: 'root', children: [] }],
            radius: ['15%', '80%'],
            label: { color: '#fff', fontSize: 11, rotate: 'radial' },
            itemStyle: {
                borderRadius: 4,
                borderColor: '#d0dce8',
                borderWidth: 2,
            },
            levels: [
                {},
                { r0: '15%', r: '40%', label: { rotate: 'tangential' } },
                { r0: '40%', r: '60%' },
                { r0: '60%', r: '80%', label: { rotate: 'tangential', fontSize: 10 } },
            ],
        }],
        ...opts.extraOptions,
    };
}

/* ========================================================================
   14. 涟漪散点图
   ======================================================================== */
function buildEffectScatterOption(data, opts) {
    const points = data.points || (data.labels && data.values ? data.labels.map((x,i)=>[x,data.values[i]]) : []);
    const C = getColors();
    return { ...CHART_THEME, tooltip:{trigger:'item'}, grid:{left:'3%',right:'4%',bottom:'3%',containLabel:true},
        xAxis:{splitLine:{lineStyle:{color:'#d0dce8'}},axisLabel:{color:'#8892a8'}},
        yAxis:{splitLine:{lineStyle:{color:'#d0dce8'}},axisLabel:{color:'#8892a8'}},
        series:[{type:'effectScatter',symbolSize:val=>Math.max(5,val/10),data:points,rippleEffect:{brushType:'stroke'},itemStyle:{color:C[0]}}],
        ...opts.extraOptions };
}

/* ========================================================================
   15. K线图
   ======================================================================== */
function buildCandlestickOption(data, opts) {
    const C = getColors();
    const vals = data.values || [];
    return { ...CHART_THEME, tooltip:{trigger:'axis'}, grid:{left:'3%',right:'4%',bottom:'3%',containLabel:true},
        xAxis:{type:'category',data:data.labels||[],axisLabel:{color:'#8892a8'}},
        yAxis:{type:'value',splitLine:{lineStyle:{color:'#d0dce8'}},axisLabel:{color:'#8892a8'}},
        series:[{type:'candlestick',data:vals,itemStyle:{color:C[1],color0:C[2],borderColor:C[1],borderColor0:C[2]}}],
        ...opts.extraOptions };
}

/* ========================================================================
   16. 象形柱图
   ======================================================================== */
function buildPictorialBarOption(data, opts) {
    const C = getColors();
    return { ...CHART_THEME, tooltip:{trigger:'axis'}, grid:{left:'3%',right:'4%',bottom:'3%',containLabel:true},
        xAxis:{type:'category',data:data.labels||[],axisLabel:{color:'#8892a8'}},
        yAxis:{type:'value',splitLine:{lineStyle:{color:'#d0dce8'}},axisLabel:{color:'#8892a8'}},
        series:[{type:'pictorialBar',data:(data.values||[]).map((v,i)=>({value:v,symbol:'circle',symbolSize:30,itemStyle:{color:C[i%C.length]}}))}],
        ...opts.extraOptions };
}

/* ========================================================================
   17. 关系图
   ======================================================================== */
function buildGraphOption(data, opts) {
    const C = getColors();
    return { ...CHART_THEME, tooltip:{},
        series:[{type:'graph',layout:'force',data:data.nodes||(data.labels||[]).map((n,i)=>({name:n,value:data.values?.[i]||0})),
            links:data.links||[],roam:true,draggable:true,
            itemStyle:{color:C[0]},label:{show:true,color:'#2a3a4a',fontSize:11},
            force:{repulsion:300,edgeLength:80}}], ...opts.extraOptions };
}

/* ========================================================================
   18. 主题河流图
   ======================================================================== */
function buildThemeRiverOption(data, opts) {
    const C = getColors();
    return { ...CHART_THEME, tooltip:{trigger:'axis'},
        singleAxis:{type:'category',bottom:'5%',axisLabel:{color:'#8892a8'}},
        series:[{type:'themeRiver',data:(data.labels||[]).map((l,i)=>(data.values?.[i]!==undefined?[l,data.values[i],'系列1']:[l,0,'系列1']))}],
        ...opts.extraOptions };
}

/* ============================================================================
   Auto-detect chart type from data
   ============================================================================ */
function suggestChartType(labels, values, data) {
    if (!labels || !values) return 'bar';
    if (values.length <= 1) return 'gauge';
    if (labels.length <= 6 && data && data.seriesNames) return 'radar';
    if (labels.length <= 10) return 'pie';
    if (labels.length > 20) return 'line';
    return 'bar';
}
