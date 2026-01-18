'use client';

import { useState } from 'react';
import { clsx } from 'clsx';
import { Send, Bot, Newspaper, Activity } from 'lucide-react'; 

const TABS = [
  { id: 'fundamentals', label: '基本面', icon: Activity },
  { id: 'news', label: '舆情', icon: Newspaper },
  { id: 'experts', label: '大师会诊', icon: Bot },
];

export default function AIResearcherPanel({ symbol }: { symbol: string }) {
  const [activeTab, setActiveTab] = useState('experts');
  const [input, setInput] = useState('');

  return (
    <div className="h-full flex flex-col bg-[#111417] border-l border-[#1e2226]">
      {/* 顶部标题栏 */}
      <div className="h-12 px-4 border-b border-[#1e2226] bg-[#080a0d] flex justify-between items-center shrink-0">
        <h3 className="text-[#1861ff] font-bold text-xs flex items-center gap-2 uppercase tracking-tighter">
          ✨ AI RESEARCHER
          <span className="text-[9px] bg-[#1861ff]/10 text-[#1861ff] px-1.5 py-0.5 rounded-sm border border-[#1861ff]/30 font-mono">
            V2.1
          </span>
        </h3>
      </div>

      {/* Tabs - 仿微牛导航 */}
      <div className="flex border-b border-[#1e2226] bg-[#111417] shrink-0">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={clsx(
                "flex-1 py-2 text-[11px] font-bold flex flex-col items-center gap-1 transition-all border-b-2",
                activeTab === tab.id
                  ? "text-white border-[#1861ff] bg-[#1861ff]/5"
                  : "text-[#8a8d91] border-transparent hover:text-white"
              )}
            >
              <Icon size={12} />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* 内容区域 */}
      <div className="flex-1 overflow-y-auto p-4 custom-scrollbar bg-[#080a0d]/30">
        {activeTab === 'fundamentals' && (
          <div className="space-y-3">
            <div className="p-3 bg-[#1c2127]/40 rounded-sm border border-[#1e2226]">
              <h4 className="text-[#d1d1d1] text-xs font-bold mb-2 flex items-center gap-2">📊 杜邦分析摘要</h4>
              <p className="text-[11px] text-[#8a8d91] leading-relaxed">
                ROE: <span className="text-[#00c087] font-mono font-bold">12.5%</span> (YoY +1.2%)
                <br/>
                净利率提升主要由产品提价驱动。
              </p>
            </div>
          </div>
        )}

        {activeTab === 'news' && (
          <div className="space-y-2">
             {[1,2,3].map(i => (
               <div key={i} className="p-3 bg-[#1c2127]/40 border-b border-[#1e2226] hover:bg-[#1c2127]/60 cursor-pointer transition">
                  <div className="text-[10px] text-[#8a8d91] mb-1 font-mono">2025-12-31 14:30</div>
                  <div className="text-xs text-[#d1d1d1] line-clamp-2 leading-normal">
                    关于获得重要客户订单的公告，预计对明年业绩产生积极影响...
                  </div>
               </div>
             ))}
          </div>
        )}

        {activeTab === 'experts' && (
          <div className="space-y-5">
            {/* 陶博士 Agent */}
            <div className="flex gap-2">
              <div className="w-7 h-7 rounded bg-[#1861ff] flex items-center justify-center text-[10px] font-black text-white shrink-0 shadow-lg shadow-blue-900/20">陶</div>
              <div className="bg-[#1c2127]/60 p-3 rounded-r rounded-bl border border-[#1e2226]">
                <div className="text-[10px] text-[#1861ff] font-bold mb-1">TREND ANALYST (陶博士)</div>
                <p className="text-[11px] text-[#d1d1d1] leading-relaxed font-medium">
                  RPS(20): <span className="text-[#ffb11a] font-mono font-bold">98</span> 超级强势。
                  <br/>
                  当前形态：口袋支点 + 窒息量萎缩。
                  <br/>
                  👉 <span className="text-[#ff4d4d] font-bold">结论：建议买入，止损10D线。</span>
                </p>
              </div>
            </div>

            {/* 巴菲特 Agent */}
            <div className="flex gap-2 flex-row-reverse">
              <div className="w-7 h-7 rounded bg-[#ffb11a] flex items-center justify-center text-[10px] font-black text-white shrink-0">巴</div>
              <div className="bg-[#1c2127]/60 p-3 rounded-l rounded-br border border-[#1e2226] text-right">
                <div className="text-[10px] text-[#ffb11a] font-bold mb-1">VALUE GUARD (巴菲特)</div>
                <p className="text-[11px] text-[#d1d1d1] leading-relaxed text-left font-medium">
                  护城河不足 (ROE 12%)，PE 50x 过高。
                  <br/>
                  👉 <span className="text-[#8a8d91] font-bold">结论：安全边际不足，建议观望。</span>
                </p>
              </div>
            </div>

            {/* 系统总结 */}
            <div className="bg-[#1861ff]/5 p-3 rounded-sm border border-[#1861ff]/20">
              <div className="text-[10px] text-[#1861ff] font-black mb-1 uppercase">Judge Consensus</div>
              <div className="text-[11px] text-[#d1d1d1] leading-relaxed">
                Score: <span className="text-[#ffb11a] font-mono font-bold">8.5/10</span>. 短强长调。
              </div>
            </div>
          </div>
        )}
      </div>

      {/* 底部输入框 */}
      <div className="p-3 border-t border-[#1e2226] bg-[#080a0d] shrink-0">
        <div className="relative">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="ASK AI..."
            className="w-full bg-[#111417] border border-[#1e2226] rounded-sm pl-3 pr-10 py-2 text-[11px] text-white focus:outline-none focus:border-[#1861ff] transition-colors placeholder:text-[#30363d]"
          />
          <button className="absolute right-2 top-2 text-[#8a8d91] hover:text-[#1861ff]">
            <Send size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}