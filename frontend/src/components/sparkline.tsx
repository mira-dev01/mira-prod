type SparklineProps = {
  data: number[];
  width?: number;
  height?: number;
  colorVar?: string;
};

export function Sparkline({ data, width = 80, height = 24, colorVar = "--primary" }: SparklineProps) {
  const stroke = `var(${colorVar})`;

  if (data.length === 0) {
    return <svg width={width} height={height} aria-hidden="true" />;
  }

  const min = Math.min(...data);
  const max = Math.max(...data);

  const points =
    min === max
      ? data.map((_, i) => {
          const x = data.length === 1 ? width / 2 : (i / (data.length - 1)) * width;
          return `${x},${height / 2}`;
        })
      : data.map((value, i) => {
          const x = data.length === 1 ? width / 2 : (i / (data.length - 1)) * width;
          const y = height - ((value - min) / (max - min)) * height;
          return `${x},${y}`;
        });

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <polyline
        points={points.join(" ")}
        fill="none"
        stroke={stroke}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
