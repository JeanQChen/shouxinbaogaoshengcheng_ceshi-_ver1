# aspect → 材料覆盖矩阵（六态）

| 状态 | 含义 |
|---|---|
| `obtained` | 已取得材料 |
| `seed_only` | 只有 seed、尚未完成扩读 |
| `authority_failed` | 权威不通过 |
| `boundary_incomplete` | 集合边界不完整 |
| `unread_scope` | 未读取范围 |
| `not_covered` | 本轮样本未覆盖 |

| aspect_id | 状态 |
|---|---|
| `company_finance.notes_to_financial_statements` | `seed_only` |

## company_finance.notes_to_financial_statements（状态 `seed_only`）

### 正式材料 mat-d71b480f83f65eb0686d68cc9dacf74e

- 材料类型：`evidence_span` · authority：`authoritative`
- component evidence_id：`e6b14793486ce974a8291e94f1489b05`

```
正文[paragraph]: 七、合并财务报表项目注释
  
  1、货币资金
  
  单位：千元
    项目  期末余额  期初余额
   库存现金  373  1,277
   银行存款  274,816,769  238,458,317
  
  其他货币资金  28,694,852  25,846,921
   合计  303,511,993  264,306,515
    其中：存放在境外的款项总额  35,198,866  20,416,787
  
  说明：期末除保证金  22,319,711千元、质押定期存款  1,019,843千元外，不存在质押或冻结、或存放在境外且资金汇回受到
  限制的款项。
  
  2、交易性金融资产
  
  单位：千元
    项目  期末余额  期初余额
   银行理财产品及结构性存款  14,282,253  7,767
   合计  14,282,253  7,767
  
  3、应收票据
  
  （1） 应收票据分类列示
  
  单位：千元
    项目  期末余额  期初余额
   银行承兑票据  130,403  1,751,725
   合计  130,403  1,751,725
  
  （2） 期末公司已质押的应收票据
  
  单位：千元
    项目  期末已质押金额
   银行承兑票据  130,403
   合计  130,403
  
  说明：期末用于质押或担保的应收票据详见 “第十节 财务报告”之“ 七、合并财务报表项目注释”“24、所有权或使用权受到
  限制的资产”。
  
```

**context_candidate（不推进 coverage、不计正式材料）**：`mat-0f86d6a0a73c7a9d86a964b37d00a0b9`, `mat-13cb6dc20082053978d9d1df44f862e8`, `mat-34ba40bc8b93bb2e178b04bf1cf34220`, `mat-36721d81d70d3a2d3d764d48dcfc8ac8`, `mat-377fcda630a5484eb18bed6e4478ae5b`, `mat-3e9367762df9b629018ed7fb9ec13754`, `mat-41c69464b4d309559a86eb5fc1c61990`, `mat-5eaa00c66bc0d9e4162e66b73cc32547`, `mat-6a858c02a9226b31a56d4dd81aa546e5`, `mat-a244a3560419786e28e59e407bd04c67`, `mat-a2c9a62e8a54147457b6678de66fdd31`, `mat-b1bd6df27d68aa7569794eb89bdeb814`, `mat-d4c94189b8b3feec64c4a0efd8813cfe`, `mat-ee55c04fe4f0aebd9df0f7f432f2d779`
