import { useEffect, useRef, useState } from "react";
import { Select } from "antd";
import { listProducts } from "../api/master";

interface Props {
  value?: number;
  onChange?: (value: number | undefined) => void;
  /** 编辑已有单据时用来回显名称（否则只显示 id，用户根本认不出是哪个商品） */
  initial?: { id: number; name: string } | null;
  disabled?: boolean;
  placeholder?: string;
}

interface Option {
  label: string;
  value: number;
}

/**
 * 商品选择器（远程搜索）—— 单据明细里选商品用。
 *
 * ★不一次性拉全量商品：几千个 SKU 全塞进 Select 会让抽屉卡死，且搜不到自己想要的。
 * ★输入防抖 250ms；搜索失败不打断录入（商品名可能是手输过半的中间态）。
 */
export default function ProductSelect({ value, onChange, initial, disabled, placeholder }: Props) {
  const [options, setOptions] = useState<Option[]>([]);
  const [loading, setLoading] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!initial || !initial.id) {
      return;
    }
    setOptions((prev) => (prev.some((item) => item.value === initial.id)
      ? prev
      : [{ label: initial.name, value: initial.id }, ...prev]));
  }, [initial?.id, initial?.name]);

  useEffect(() => {
    void search("");
    return () => window.clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function search(keyword: string) {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      setLoading(true);
      try {
        const data = await listProducts({ keyword: keyword || undefined, page_size: 20 });
        setOptions(data.list.map((p) => ({
          label: p.barcode ? `${p.name}（${p.barcode}）` : p.name,
          value: p.id,
        })));
      } catch {
        // 交给全局提示，选择器保持上一次结果
      } finally {
        setLoading(false);
      }
    }, 250);
  }

  return (
    <Select<number>
      showSearch
      allowClear
      value={value}
      disabled={disabled}
      placeholder={placeholder ?? "搜索商品名称或条码"}
      filterOption={false}
      onSearch={(kw) => void search(kw)}
      onChange={(next) => onChange?.(next)}
      options={options}
      loading={loading}
      notFoundContent={loading ? "搜索中…" : "没有匹配的商品"}
      style={{ width: "100%" }}
    />
  );
}
